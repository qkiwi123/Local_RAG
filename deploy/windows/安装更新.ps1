$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'payload/launcher/update_ui.ps1') -Bootstrap
exit $LASTEXITCODE
