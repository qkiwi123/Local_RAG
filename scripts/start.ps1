param(
    [switch]$Reload,
    [switch]$Stop,
    [switch]$LegacyFrontend,
    [switch]$NoBrowser,
    [ValidateSet("gpu", "cpu")]
    [string]$InferenceProfile = "gpu"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$logsDir = Join-Path $repoRoot "logs"
$pidFile = Join-Path $logsDir "service.pids"

# 一键关闭：直接转发给 stop.ps1 后退出。
if ($Stop) {
    & (Join-Path $PSScriptRoot "stop.ps1")
    exit 0
}

if ($InferenceProfile -eq "gpu") {
    $virtualEnv = ".venv-gpu"
    $env:EMBEDDING_DEVICE = "cuda:0"
    $env:EMBEDDING_BATCH_SIZE = "32"
    $env:RERANKER_DEVICE = "cuda:0"
    $env:RERANKER_USE_FP16 = "true"
}
else {
    $virtualEnv = ".venv"
    $env:EMBEDDING_DEVICE = "cpu"
    $env:EMBEDDING_BATCH_SIZE = "32"
    $env:RERANKER_DEVICE = "cpu"
    $env:RERANKER_USE_FP16 = "false"
}
$python = Join-Path $repoRoot "$virtualEnv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "未找到 $InferenceProfile 推理环境：$python，请先安装对应虚拟环境"
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
$env:PYTHONIOENCODING = "utf-8"

# React SPA：每次启动都重新构建，避免已有 web/dist 让源码更新后仍托管旧页面。
Write-Host "[前端] 正在重新构建 web/（npm run build）..."
Push-Location (Join-Path $repoRoot "web")
try {
    & npm.cmd run build | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "前端构建失败，请检查 npm 环境或手动执行 cd web; npm install; npm run build"
    }
}
finally {
    Pop-Location
}
$spaBuildStamp = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()

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

$backendReady = $false
$backendHealthUrl = "http://127.0.0.1:8000/api/health"
for ($attempt = 1; $attempt -le 240; $attempt++) {
    $backend.Refresh()
    if ($backend.HasExited) {
        throw "后端启动失败，请查看日志：$backendStderr"
    }

    $health = $null
    try {
        $health = Invoke-RestMethod `
            -Uri $backendHealthUrl `
            -UseBasicParsing `
            -TimeoutSec 1
    }
    catch {
        Start-Sleep -Milliseconds 500
        continue
    }

    if ($health.status -ne "ok") {
        Start-Sleep -Milliseconds 500
        continue
    }

    $warmupStatus = $health.model_warmup.status
    if ($warmupStatus -in @("ready", "disabled")) {
        $backendReady = $true
        break
    }
    if ($warmupStatus -eq "failed") {
        $component = $health.model_warmup.component
        $errorType = $health.model_warmup.error_type
        $detail = $health.model_warmup.detail
        & taskkill.exe /PID $backend.Id /T /F 2>$null | Out-Null
        throw (
            "后端模型预热失败（$component / $errorType）：$detail；" +
            "请查看日志：$backendStderr"
        )
    }

    Start-Sleep -Milliseconds 500
}

if (-not $backendReady) {
    & taskkill.exe /PID $backend.Id /T /F 2>$null | Out-Null
    throw "等待后端就绪超时，请查看日志：$backendStderr"
}

# 记录 PID 与进程启动时间，供 stop.ps1 校验后一键关闭（防 PID 复用误杀）。
$pidRecords = @(
    "backend=$($backend.Id)|$($backend.StartTime.ToString('o'))"
)

$frontend = $null
$frontendStderr = $null
if ($LegacyFrontend) {
    # 旧 Streamlit 只用于人工对照；headless 避免它抢先打开 8501 页面。
    $env:BACKEND_URL = "http://127.0.0.1:8000"
    $frontendStdout = Join-Path $logsDir "frontend.out.log"
    $frontendStderr = Join-Path $logsDir "frontend.error.log"
    $frontend = Start-Process `
        -FilePath $python `
        -ArgumentList @(
            "-m", "streamlit", "run", "frontend/app.py",
            "--server.headless", "true"
        ) `
        -WorkingDirectory $repoRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $frontendStdout `
        -RedirectStandardError $frontendStderr `
        -PassThru
    $pidRecords += "frontend=$($frontend.Id)|$($frontend.StartTime.ToString('o'))"
}

$pidRecords | Set-Content -LiteralPath $pidFile -Encoding utf8

$reactUrl = "http://localhost:8000/?build=$spaBuildStamp"
if (-not $NoBrowser) {
    try {
        Start-Process $reactUrl -ErrorAction Stop
    }
    catch {
        Write-Warning "无法自动打开浏览器，请手动访问 React SPA：$reactUrl"
    }
}

Write-Host "Backend PID: $($backend.Id)"
Write-Host "Backend/model health: ready"
Write-Host "Inference profile: $InferenceProfile ($virtualEnv)"
Write-Host "Embedding device: $env:EMBEDDING_DEVICE"
Write-Host "Reranker device: $env:RERANKER_DEVICE; FP16: $env:RERANKER_USE_FP16"
Write-Host "React SPA (default): $reactUrl"
Write-Host "API docs: http://localhost:8000/docs"
Write-Host "Backend log: $backendStderr"
if ($LegacyFrontend) {
    Write-Host "Legacy Streamlit PID: $($frontend.Id) http://localhost:8501"
    Write-Host "Legacy frontend log: $frontendStderr"
}
else {
    Write-Host "Legacy Streamlit: not started (use -LegacyFrontend only for comparison)"
}
if ($NoBrowser) {
    Write-Host "Browser auto-open: disabled"
}
if ($Reload) {
    Write-Host "Backend hot reload: enabled"
}
Write-Host "停止服务：.\scripts\stop.ps1（或 .\scripts\start.ps1 -Stop）"
