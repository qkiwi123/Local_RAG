param([Parameter(Mandatory)][string]$BaseDirectory, [string]$Output, [Parameter(Mandatory)][string]$Notes, [switch]$AllowWorkingTree)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Output) {
    $preference = Join-Path $projectRoot 'build/windows-release/output-preference.json'
    $folder = Join-Path $projectRoot 'build/windows-update/exports'
    if (Test-Path -LiteralPath $preference) { $folder = (Get-Content -LiteralPath $preference -Raw | ConvertFrom-Json).output_directory }
    $Output = Join-Path $folder ('LocalRAG-app-update-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.zip')
}
$python = Join-Path $projectRoot '.venv/Scripts/python.exe'
$arguments = @('-B',(Join-Path $projectRoot 'deploy/windows/build_update.py'),'--base',$BaseDirectory,'--output',$Output,'--notes',$Notes)
if ($AllowWorkingTree) { $arguments += '--allow-working-tree' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw '本地应用更新包构建失败，已有输出保留。' }
