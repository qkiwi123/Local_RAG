param(
    [switch]$Reload,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$logsDir = Join-Path $repoRoot "logs"
$pidFile = Join-Path $logsDir "service.pids"

# 一键关闭：直接转发给 stop.ps1 后退出。
if ($Stop) {
    & (Join-Path $PSScriptRoot "stop.ps1")
    exit 0
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到项目 Python：$python，请先创建 .venv 并安装 requirements.txt"
}

# 防双开：PID 文件里仍有存活且启动时间匹配的记录时拒绝再次启动。
if (Test-Path -LiteralPath $pidFile) {
    $alive = @()
    foreach ($line in Get-Content -LiteralPath $pidFile) {
        if ($line -match '^(backend|frontend)=(\d+)\|(.+)$') {
            $recordedPid = [int]$matches[2]
            $process = Get-Process -Id $recordedPid -ErrorAction SilentlyContinue
            if ($process) {
                $isMatch = $false
                try {
                    $isMatch = ($process.StartTime - [datetime]$matches[3]).TotalSeconds -lt 2
                }
                catch {
                    $isMatch = $false
                }
                if ($isMatch) {
                    $alive += $matches[1]
                }
            }
        }
    }
    if ($alive.Count -gt 0) {
        throw "服务已在运行（$($alive -join ', ')），请先执行 .\scripts\stop.ps1 停止后再启动。"
    }
}

New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
$backendStdout = Join-Path $logsDir "backend.out.log"
$backendStderr = Join-Path $logsDir "backend.error.log"
$frontendStdout = Join-Path $logsDir "frontend.out.log"
$frontendStderr = Join-Path $logsDir "frontend.error.log"
$env:PYTHONIOENCODING = "utf-8"

$backendArgs = @("-m", "uvicorn", "backend.main:app")
if ($Reload) {
    $backendArgs += @("--reload", "--reload-dir", "backend")
}

$backend = Start-Process `
    -FilePath $python `
    -ArgumentList $backendArgs `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $backendStdout `
    -RedirectStandardError $backendStderr `
    -PassThru

$backendHealthUrl = "http://127.0.0.1:8000/"
$backendReady = $false
for ($attempt = 1; $attempt -le 60; $attempt++) {
    $backend.Refresh()
    if ($backend.HasExited) {
        throw "后端启动失败，请查看日志：$backendStderr"
    }

    try {
        $health = Invoke-WebRequest `
            -Uri $backendHealthUrl `
            -UseBasicParsing `
            -TimeoutSec 1
        if ($health.StatusCode -eq 200) {
            $backendReady = $true
            break
        }
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

if (-not $backendReady) {
    throw "等待后端就绪超时，请查看日志：$backendStderr"
}

# 前端固定访问与 Uvicorn 监听一致的 IPv4 地址，避免 localhost 解析差异。
$env:BACKEND_URL = "http://127.0.0.1:8000"

$frontend = Start-Process `
    -FilePath $python `
    -ArgumentList @("-m", "streamlit", "run", "frontend/app.py") `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $frontendStdout `
    -RedirectStandardError $frontendStderr `
    -PassThru

# 记录 PID 与进程启动时间，供 stop.ps1 校验后一键关闭（防 PID 复用误杀）。
@(
    "backend=$($backend.Id)|$($backend.StartTime.ToString('o'))",
    "frontend=$($frontend.Id)|$($frontend.StartTime.ToString('o'))"
) | Set-Content -LiteralPath $pidFile -Encoding utf8

Write-Host "Backend PID: $($backend.Id)  http://localhost:8000/docs"
Write-Host "Backend health: ready"
Write-Host "Frontend PID: $($frontend.Id) http://localhost:8501"
Write-Host "Backend log: $backendStderr"
Write-Host "Frontend log: $frontendStderr"
if ($Reload) {
    Write-Host "Backend hot reload: enabled"
}
Write-Host "停止服务：.\scripts\stop.ps1（或 .\scripts\start.ps1 -Stop）"
