# 一键关闭 start.ps1 启动的前后端服务。
#
# 读取 logs/service.pids，对每个记录：
#   1. 进程不存在 → 提示已停止；
#   2. 进程存在但启动时间与记录不符 → 跳过并警告（防止 PID 被复用后误杀其他进程）；
#   3. 匹配 → taskkill /T /F 终止整个进程树（覆盖 uvicorn --reload 子进程）。
# 处理完成后删除 PID 文件。

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot "logs\service.pids"

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

foreach ($record in $records) {
    $process = Get-Process -Id $record.Pid -ErrorAction SilentlyContinue
    if (-not $process) {
        Write-Host "$($record.Name)（PID $($record.Pid)）：已不在运行，跳过。"
        continue
    }

    $isMatch = $false
    try {
        $isMatch = ($process.StartTime - $record.StartTime).TotalSeconds -lt 2
    }
    catch {
        $isMatch = $false
    }
    if (-not $isMatch) {
        Write-Warning (
            "$($record.Name)（PID $($record.Pid)）：进程存在但启动时间与记录不符，" +
            "已跳过以防误杀。请手动确认该进程后处理。"
        )
        continue
    }

    & taskkill.exe /PID $record.Pid /T /F *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "$($record.Name)（PID $($record.Pid)）：已停止。"
    }
    else {
        Write-Warning (
            "$($record.Name)（PID $($record.Pid)）：停止失败，" +
            "taskkill 退出码 $LASTEXITCODE。"
        )
    }
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "PID 文件已清理。"
