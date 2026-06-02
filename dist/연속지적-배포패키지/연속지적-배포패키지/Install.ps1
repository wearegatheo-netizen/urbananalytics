param(
    [string]$InstallRoot
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms

function U([int[]]$Codes) {
    return -join ($Codes | ForEach-Object { [char]$_ })
}

$packageRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$documents = [Environment]::GetFolderPath("MyDocuments")
if (-not $documents -or -not (Test-Path -LiteralPath $documents)) {
    $documents = $packageRoot
}
$defaultInstallRoot = Join-Path $documents "CadastreAutomation"
if (-not $InstallRoot) {
    New-Item -ItemType Directory -Force -Path $defaultInstallRoot | Out-Null
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = "Select the folder where CadastreAutomation will be installed."
    $dialog.SelectedPath = $defaultInstallRoot
    $dialog.ShowNewFolderButton = $true
    $result = $dialog.ShowDialog()
    if ($result -ne [System.Windows.Forms.DialogResult]::OK -or -not $dialog.SelectedPath) {
        Write-Host "Installation canceled."
        exit 1
    }
    $InstallRoot = $dialog.SelectedPath
}
$installRoot = $InstallRoot
$appSource = Join-Path $packageRoot "app"
$stylesSource = Join-Path $packageRoot "styles"
$appTarget = Join-Path $installRoot "app"
$logPath = Join-Path $packageRoot "install.log"

Start-Transcript -LiteralPath $logPath -Force | Out-Null
try {
    if (-not (Test-Path -LiteralPath $appSource)) {
        throw "The app folder was not found. Please unzip the package before running this installer."
    }

    New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
    Copy-Item -LiteralPath $appSource -Destination $installRoot -Recurse -Force
    if (Test-Path -LiteralPath $stylesSource) {
        Copy-Item -LiteralPath $stylesSource -Destination $installRoot -Recurse -Force
    }

    $launcher = Join-Path $appTarget "launch_cadastre_gui.vbs"
    if (-not (Test-Path -LiteralPath $launcher)) {
        throw "Launcher was not found: $launcher"
    }

    $desktopCandidates = @(
        [Environment]::GetFolderPath("Desktop"),
        [Environment]::GetFolderPath("DesktopDirectory"),
        (Join-Path $env:USERPROFILE "OneDrive\Desktop"),
        (Join-Path $env:USERPROFILE "Desktop")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $desktop = $desktopCandidates | Select-Object -First 1
    if (-not $desktop) {
        $desktop = Join-Path $env:USERPROFILE "Desktop"
        New-Item -ItemType Directory -Force -Path $desktop | Out-Null
    }

    $shortcutName = (U @(0xC5F0, 0xC18D, 0xC9C0, 0xC801)) + ".lnk"
    $shortcutPath = Join-Path $desktop $shortcutName
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$env:WINDIR\system32\wscript.exe"
    $shortcut.Arguments = '"' + $launcher + '"'
    $shortcut.WorkingDirectory = $appTarget
    $shortcut.Description = "Cadastre automation"
    $shortcut.Save()

    Write-Host "Install completed."
    Write-Host "Install path: $installRoot"
    Write-Host "Shortcut: $shortcutPath"
    Write-Host ""
    Write-Host "Required: QGIS 3.x, Chrome or Edge"
} finally {
    Stop-Transcript | Out-Null
}
