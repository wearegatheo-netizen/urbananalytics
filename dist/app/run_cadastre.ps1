param(
    [Parameter(Mandatory = $true)]
    [string]$Address,

    [string]$VWorldKey = $env:VWORLD_API_KEY,
    [double]$X,
    [double]$Y,
    [string]$Sido,
    [string]$Sigungu,
    [double]$Radius = 5000,
    [string]$OutDir = "$PSScriptRoot\outputs",
    [string]$Style,
    [string]$InputZip,
    [switch]$OpenQgis,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Find-QgisPython {
    $candidates = @(
        "C:\Program Files\QGIS 3.34.2\bin\python-qgis.bat",
        "C:\Program Files\QGIS 3.8\bin\python-qgis.bat"
    )
    $programRoots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    foreach ($root in $programRoots) {
        $candidates += Get-ChildItem -LiteralPath $root -Directory -Filter "QGIS*" -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "bin\python-qgis.bat" }
    }
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw "QGIS 실행 환경을 찾지 못했습니다. QGIS 3.x를 설치한 뒤 다시 실행해주세요."
}

function Get-DefaultStyle {
    $style = Join-Path (Split-Path -Parent $PSScriptRoot) "styles\cadastre-default.qml"
    if (Test-Path -LiteralPath $style) {
        return $style
    }
    return $null
}

$qgisPython = Find-QgisPython
if (-not $Style) {
    $Style = Get-DefaultStyle
}

$script = Join-Path $PSScriptRoot "cadastre_automation.py"
$argsList = @(
    $script,
    "--address", $Address,
    "--radius", "$Radius",
    "--out-dir", $OutDir
)

if ($Style) {
    $argsList += @("--style", $Style)
}

if ($VWorldKey) {
    $argsList += @("--vworld-key", $VWorldKey)
} else {
    if (-not $PSBoundParameters.ContainsKey("X") -or -not $PSBoundParameters.ContainsKey("Y") -or -not $Sido) {
        throw "VWorldKey가 없으면 -X, -Y, -Sido를 함께 넣어주세요."
    }
    $argsList += @("--x", "$X", "--y", "$Y", "--sido", $Sido)
    if ($Sigungu) {
        $argsList += @("--sigungu", $Sigungu)
    }
}

if ($OpenQgis) {
    $argsList += "--open-qgis"
}

if ($InputZip) {
    $argsList += @("--input-zip", $InputZip)
}

if ($DryRun) {
    $argsList += "--dry-run"
}

& $qgisPython @argsList
