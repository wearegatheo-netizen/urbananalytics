$ErrorActionPreference = "Stop"

$desktopCandidates = @(
    [Environment]::GetFolderPath("Desktop"),
    [Environment]::GetFolderPath("DesktopDirectory"),
    (Join-Path $env:USERPROFILE "OneDrive\Desktop"),
    (Join-Path $env:USERPROFILE "Desktop")
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

$desktop = $desktopCandidates | Select-Object -First 1
if (-not $desktop) {
    throw "바탕화면 경로를 찾지 못했습니다."
}

function U([int[]]$Codes) {
    return -join ($Codes | ForEach-Object { [char]$_ })
}

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$target = Join-Path $workspace "launch_cadastre_map.vbs"
if (-not (Test-Path -LiteralPath $target)) {
    throw "실행 파일을 찾지 못했습니다: $target"
}

$shortcutName = (U @(0xC5F0,0xC18D,0xC9C0,0xC801,0x0020,0xC704,0xCE58,0xB3C4)) + ".lnk"
$shortcutPath = Join-Path $desktop $shortcutName
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$shortcut.Arguments = "`"$target`""
$shortcut.WorkingDirectory = $workspace
$shortcut.Description = "Cadastre satellite map launcher"
$shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,13"
$shortcut.Save()

Write-Host $shortcutPath
