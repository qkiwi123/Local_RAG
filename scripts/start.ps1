param(
    [switch]$Reload,
    [switch]$Stop,
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
$frontendStdout = Join-Path $logsDir "frontend.out.log"
$frontendStderr = Join-Path $logsDir "frontend.error.log"
$env:PYTHONIOENCODING = "utf-8"

# React SPA：未构建时自动构建一次，产物由后端静态托管。
$webDistIndex = Join-Path $repoRoot "web\dist\index.html"
if (-not (Test-Path -LiteralPath $webDistIndex)) {
    Write-Host "[前端] 未找到构建产物，正在构建 web/（npm run build）..."
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
}

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

Write-Host "Backend PID: $($backend.Id)"
Write-Host "Backend health: ready"
Write-Host "Inference profile: $InferenceProfile ($virtualEnv)"
Write-Host "Embedding device: $env:EMBEDDING_DEVICE"
Write-Host "Reranker device: $env:RERANKER_DEVICE; FP16: $env:RERANKER_USE_FP16"
Write-Host "React SPA: http://localhost:8000/"
Write-Host "API docs: http://localhost:8000/docs"
Write-Host "Legacy Streamlit PID: $($frontend.Id) http://localhost:8501"
Write-Host "Backend log: $backendStderr"
Write-Host "Frontend log: $frontendStderr"
if ($Reload) {
    Write-Host "Backend hot reload: enabled"
}
Write-Host "停止服务：.\scripts\stop.ps1（或 .\scripts\start.ps1 -Stop）"
