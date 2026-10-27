param([string]$Root, [switch]$Bootstrap)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
try {
    if ($Bootstrap) {
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = '选择当前正在使用的 RAG 便携目录（内有 runtime 和 data）'
        if ($dialog.ShowDialog() -ne 'OK') { exit 0 }
        $Root = $dialog.SelectedPath
    }
    if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
    $Root = (Resolve-Path -LiteralPath $Root).Path
    $python = Join-Path $Root 'runtime/python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw '此目录没有随包运行环境，请重新选择便携目录。' }
    $entry = Join-Path $PSScriptRoot 'update_entry.py'
    $arguments = @('-I','-B','-u',$entry,'--root',$Root)
    if ($Bootstrap) {
        $packageDirectory = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
        $arguments += @('--directory',$packageDirectory)
    } elseif (Test-Path -LiteralPath (Join-Path $Root 'state/update-active.json')) {
        $arguments += '--recover'
    } else {
        $picker = New-Object System.Windows.Forms.OpenFileDialog
        $picker.Title = '选择本地应用更新包'
        $picker.Filter = '应用更新包 (*.zip)|*.zip'
        if ($picker.ShowDialog() -ne 'OK') { exit 0 }
        $arguments += @('--package',$picker.FileName)
    }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw '更新尚未完成，请查看提示及 state/updates 下的恢复记录。' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    Read-Host '按回车关闭此窗口'
}
