# 一键关闭 start.ps1 启动的前后端服务。
#
# 读取 logs/service.pids，对每个记录：
#   1. 进程不存在 → 提示已停止；
#   2. 进程存在但启动时间与记录不符 → 跳过并警告（防止 PID 被复用后误杀其他进程）；
#   3. 匹配 → 优先 taskkill /T /F 终止整个进程树（覆盖 uvicorn --reload 子进程）；
#      若隐藏窗口的 conhost 子进程令 taskkill 返回失败，则回退终止主进程，并清理
#      当前启动日志中经过身份校验的 Uvicorn server worker。
# 只有所有记录均已停止或原本不存在时才删除 PID 文件；失败记录会保留以便重试。

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot "logs\service.pids"
$backendStderr = Join-Path $repoRoot "logs\backend.error.log"

function Invoke-TaskkillTree {
    param([Parameter(Mandatory)][int]$ProcessId)

    # taskkill 可能只因无法终止隐藏启动产生的 conhost.exe 而向 stderr 写错误。
    # 这里局部允许原生命令报错，避免全局 ErrorActionPreference=Stop 在读取
    # $LASTEXITCODE 和执行主进程回退前直接中断脚本。
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& taskkill.exe /PID $ProcessId /T /F 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [pscustomobject]@{
        ExitCode = $exitCode
        Output = $output
    }
}

function Get-UvicornWorkerRecords {
    param(
        [Parameter(Mandatory)]$BackendRecord,
        [Parameter(Mandatory)][string]$LogPath
    )

    if (-not (Test-Path -LiteralPath $LogPath)) {
        return @()
    }

    $workerIds = @(
        Get-Content -LiteralPath $LogPath -ErrorAction SilentlyContinue |
            ForEach-Object {
                if ($_ -match 'Started server process \[(\d+)\]') {
                    [int]$matches[1]
                }
            } |
            Where-Object { $_ -and $_ -ne $BackendRecord.Pid } |
            Sort-Object -Unique
    )

    $workers = @()
    foreach ($workerId in $workerIds) {
        $worker = Get-Process -Id $workerId -ErrorAction SilentlyContinue
        if (-not $worker -or $worker.ProcessName -ne "python") {
            continue
        }

        # backend.error.log 在每次 start.ps1 启动时会重新创建；仍要求 worker
        # 启动时间不早于已验证的 reloader，避免把异常日志内容当成可终止目标。
        if ($worker.StartTime -lt $BackendRecord.StartTime.AddSeconds(-2)) {
            continue
        }

        $workers += [pscustomobject]@{
            Name = "backend"
            Pid = $worker.Id
            StartTime = $worker.StartTime
        }
    }

    return $workers
}

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "未找到 PID 文件（$pidFile），服务可能未运行或未通过 start.ps1 启动。"
    exit 0
}

$records = @()
foreach ($line in Get-Content -LiteralPath $pidFile) {
    if ($line -match '^(backend|frontend)=(\d+)\|(.+)$') {
        $records += [pscustomobject]@{
            Name = $matches[1]
            Pid = [int]$matches[2]
            StartTime = [datetime]$matches[3]
        }
    }
}

if ($records.Count -eq 0) {
    Write-Host "PID 文件为空或格式无法识别，未执行任何操作：$pidFile"
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    exit 0
}

$remainingRecords = @()
foreach ($record in $records) {
    $process = Get-Process -Id $record.Pid -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Host "$($record.Name)（PID $($record.Pid)）：已不在运行，跳过。"
        continue
    }

    $isMatch = $false
    try {
        $isMatch = [Math]::Abs(($process.StartTime - $record.StartTime).TotalSeconds) -lt 2
    }
    catch {
        $isMatch = $false
    }
    if (-not $isMatch) {
        Write-Warning (
            "$($record.Name)（PID $($record.Pid)）：进程存在但启动时间与记录不符，" +
            "已跳过以防误杀。请手动确认该进程后处理。"
        )
        $remainingRecords += $record
        continue
    }

    $uvicornWorkers = @{}
    if ($record.Name -eq "backend") {
        foreach ($worker in @(Get-UvicornWorkerRecords -BackendRecord $record -LogPath $backendStderr)) {
            $uvicornWorkers[$worker.Pid] = $worker
        }
    }

    $taskkillResult = Invoke-TaskkillTree -ProcessId $record.Pid
    $process = Get-Process -Id $record.Pid -ErrorAction SilentlyContinue
    if ($process) {
        try {
            Stop-Process -Id $record.Pid -Force -ErrorAction Stop
            $process.WaitForExit(5000) | Out-Null
        }
        catch {
            # 统一在下面根据进程是否仍存活输出最终结果。
        }
    }

    # taskkill 可能因 conhost 失败，随后 Stop-Process 只结束 reloader 主进程。
    # 主进程退出后再次读取本次启动日志，捕获竞态期间刚重启的 server worker。
    if ($record.Name -eq "backend") {
        foreach ($worker in @(Get-UvicornWorkerRecords -BackendRecord $record -LogPath $backendStderr)) {
            $uvicornWorkers[$worker.Pid] = $worker
        }
    }

    $remainingWorkers = @()
    foreach ($workerRecord in $uvicornWorkers.Values) {
        $workerProcess = Get-Process -Id $workerRecord.Pid -ErrorAction SilentlyContinue
        if (-not $workerProcess) {
            continue
        }

        $isWorkerMatch = $false
        try {
            $isWorkerMatch = (
                $workerProcess.ProcessName -eq "python" -and
                [Math]::Abs(($workerProcess.StartTime - $workerRecord.StartTime).TotalSeconds) -lt 2
            )
        }
        catch {
            $isWorkerMatch = $false
        }
        if (-not $isWorkerMatch) {
            Write-Warning (
                "backend worker（PID $($workerRecord.Pid)）：进程身份已变化，" +
                "已跳过以防误杀。"
            )
            $remainingWorkers += $workerRecord
            continue
        }

        try {
            Stop-Process -Id $workerRecord.Pid -Force -ErrorAction Stop
            $workerProcess.WaitForExit(5000) | Out-Null
        }
        catch {
            # 统一在下面根据进程是否仍存活输出最终结果。
        }
        if (Get-Process -Id $workerRecord.Pid -ErrorAction SilentlyContinue) {
            Write-Warning "backend worker（PID $($workerRecord.Pid)）：停止失败。"
            $remainingWorkers += $workerRecord
        }
    }

    $stillRunning = Get-Process -Id $record.Pid -ErrorAction SilentlyContinue
    if (-not $stillRunning -and $remainingWorkers.Count -eq 0) {
        Write-Host "$($record.Name)（PID $($record.Pid)）：已停止。"
    }
    else {
        $taskkillDetail = @(
            $taskkillResult.Output |
                ForEach-Object { $_.ToString().Trim() } |
                Where-Object { $_ }
        ) -join " "
        if (-not $taskkillDetail) {
            $taskkillDetail = "无详细输出"
        }
        if ($stillRunning) {
            Write-Warning (
                "$($record.Name)（PID $($record.Pid)）：停止失败，" +
                "taskkill 退出码 $($taskkillResult.ExitCode)：$taskkillDetail"
            )
            $remainingRecords += $record
        }
        foreach ($remainingWorker in $remainingWorkers) {
            $remainingRecords += $remainingWorker
        }
    }
}

if ($remainingRecords.Count -eq 0) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Host "PID 文件已清理。"
    exit 0
}

@(
    $remainingRecords | ForEach-Object {
        "$($_.Name)=$($_.Pid)|$($_.StartTime.ToString('o'))"
    }
) | Set-Content -LiteralPath $pidFile -Encoding utf8

Write-Warning "仍有进程未处理，PID 文件已保留：$pidFile"
exit 1
