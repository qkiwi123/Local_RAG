param(
    [string]$RuntimeBundle,
    [string]$OutputDirectory,
    [string]$Name,
    [switch]$InternalPreview
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv-gpu\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Maintainer Python environment is missing.' }
$buildArguments = @('-B', (Join-Path $projectRoot 'deploy\windows\build_release.py'))
if ($RuntimeBundle) { $buildArguments += @('--runtime-bundle', $RuntimeBundle) }
if ($OutputDirectory) { $buildArguments += @('--output-directory', $OutputDirectory) }
if ($Name) { $buildArguments += @('--name', $Name) }
if ($InternalPreview) { $buildArguments += '--internal-preview' }
Push-Location $projectRoot
try {
    & $pythonPath @buildArguments
    if ($LASTEXITCODE -ne 0) { throw "Windows package build failed ($LASTEXITCODE); partial outputs preserved." }
} finally { Pop-Location }
