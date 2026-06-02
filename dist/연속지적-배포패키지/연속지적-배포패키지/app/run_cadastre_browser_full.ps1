param(
    [Parameter(Mandatory = $true)]
    [string]$Address,

    [Parameter(Mandatory = $true)]
    [string]$VWorldKey,

    [string]$VWorldId,
    [string]$VWorldPassword,
    [double]$Radius = 5000,
    [string]$OutDir,
    [string]$Style,
    [switch]$OpenQgis
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

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

function Get-DefaultOutputDir {
    $folderName = -join ([char[]](0xC5F0, 0xC18D, 0xC9C0, 0xC801))
    $desktopCandidates = @(
        [Environment]::GetFolderPath("Desktop"),
        [Environment]::GetFolderPath("DesktopDirectory"),
        (Join-Path $env:USERPROFILE "OneDrive\Desktop"),
        (Join-Path $env:USERPROFILE "Desktop")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    $desktop = $desktopCandidates | Select-Object -First 1
    if (-not $desktop) {
        $desktop = Join-Path $env:USERPROFILE "Desktop"
    }
    return Join-Path $desktop $folderName
}

function Invoke-VWorldDownload {
    param(
        [string]$DsId,
        [string]$PageDsId,
        [string]$FileNo,
        [string]$DownloadDir,
        [string]$ProfileDir,
        [switch]$UseMultiDownload,
        [string]$VWorldId,
        [string]$VWorldPassword
    )

    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
    $before = Get-ChildItem -LiteralPath $DownloadDir -Filter "*.zip" -File -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    $downloadArgs = @(
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $PSScriptRoot "download_vworld_browser.ps1"),
        "-DsId", $DsId,
        "-FileNo", $FileNo,
        "-FileSizeKb", "1",
        "-DownloadDir", $DownloadDir,
        "-ProfileDir", $ProfileDir,
        "-DownloadWaitSeconds", "300"
    )
    if ($PageDsId) {
        $downloadArgs += @("-PageDsId", $PageDsId)
    }
    if ($UseMultiDownload) {
        $downloadArgs += "-UseMultiDownload"
    }
    if ($VWorldId -and $VWorldPassword) {
        $downloadArgs += @("-VWorldId", $VWorldId, "-VWorldPassword", $VWorldPassword)
    }

    & powershell @downloadArgs
    $downloadExitCode = $LASTEXITCODE

    $zip = Get-ChildItem -LiteralPath $DownloadDir -Filter "*.zip" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notin $before } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $zip) {
        $zip = Get-ChildItem -LiteralPath $DownloadDir -Filter "*.zip" -File -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
    }
    if (-not $zip) {
        if ($downloadExitCode -ne 0) {
            Write-Host "다운로드 스크립트가 오류를 반환했습니다. 폴더를 60초 더 확인합니다."
            $extraDeadline = (Get-Date).AddSeconds(60)
            do {
                $zip = Get-ChildItem -LiteralPath $DownloadDir -Filter "*.zip" -File -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending |
                    Select-Object -First 1
                if ($zip) {
                    break
                }
                Start-Sleep -Seconds 2
            } while ((Get-Date) -lt $extraDeadline)
        }
    }
    if (-not $zip) {
        throw "Could not find a downloaded ZIP in: $DownloadDir"
    }
    return $zip
}

if (-not $OutDir) {
    $OutDir = Get-DefaultOutputDir
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Write-Host "[1/6] 주소를 좌표로 변환하고, 해당 시군구의 VWorld 연속지적 ZIP을 찾는 중입니다."
Write-Host "      이 단계에서 입력한 VWorld API 발급키를 실제로 사용합니다."

$qgisPython = Find-QgisPython
if (-not $Style) {
    $Style = Get-DefaultStyle
}

$script = Join-Path $PSScriptRoot "cadastre_automation.py"
$dryArgs = @(
    $script,
    "--address", $Address,
    "--vworld-key", $VWorldKey,
    "--radius", "$Radius",
    "--out-dir", $OutDir,
    "--dry-run"
)
if ($Style) {
    $dryArgs += @("--style", $Style)
}

$dryRunText = & $qgisPython @dryArgs 2>&1
$dryRunRaw = $dryRunText -join "`n"
$jsonStart = $dryRunRaw.IndexOf("{")
$jsonEnd = $dryRunRaw.LastIndexOf("}")
if ($jsonStart -lt 0 -or $jsonEnd -le $jsonStart) {
    if ($dryRunRaw -match "NOT_FOUND") {
        throw "Address geocoding failed. Please enter a full address including city/county/district. Example: '서울특별시 강남구 논현동 151-27번지'.`n`n$dryRunRaw"
    }
    throw "Dry-run failed before resource matching.`n`n$dryRunRaw"
}
$dryRunJson = $dryRunRaw.Substring($jsonStart, $jsonEnd - $jsonStart + 1)
$dryRun = $dryRunJson | ConvertFrom-Json
$fileNoMatch = [regex]::Match($dryRun.download_href, 'fileNo=(\d+)')
if (-not $fileNoMatch.Success) {
    throw "Could not read fileNo from download href: $($dryRun.download_href)"
}
$fileNo = $fileNoMatch.Groups[1].Value

Write-Host "주소 확인 완료: X=$($dryRun.x), Y=$($dryRun.y)"
Write-Host "다운로드 대상: $($dryRun.resource)"
Write-Host "[2/6] VWorld 브라우저를 열고 연속지적 ZIP을 다운로드하는 중입니다."

$downloadDir = Join-Path $OutDir "browser_downloads"
$profileDir = Join-Path $OutDir "chrome_vworld_profile"
$zip = Invoke-VWorldDownload -DsId "30564" -FileNo $fileNo -DownloadDir $downloadDir -ProfileDir $profileDir -VWorldId $VWorldId -VWorldPassword $VWorldPassword
Write-Host "ZIP 다운로드 완료: $($zip.FullName)"
Write-Host "[3/6] VWorld 용도지역 ZIP 4종을 다운로드하는 중입니다."

$zoneSpecs = @(
    @{ Key = "urban"; Label = "도시지역"; DsId = "20171128DS00117"; FileNo = "1507" },
    @{ Key = "management"; Label = "관리지역"; DsId = "20171128DS00118"; FileNo = "1516" },
    @{ Key = "agriculture"; Label = "농림지역"; DsId = "20171128DS00119"; FileNo = "1442" },
    @{ Key = "nature"; Label = "자연환경보전지역"; DsId = "20171128DS00120"; FileNo = "444" }
)
$zoneZipArgs = @()
foreach ($zone in $zoneSpecs) {
    Write-Host "용도지역 다운로드: $($zone.Label)"
    $zoneDownloadDir = Join-Path $OutDir ("browser_downloads\zones\" + $zone.Key)
    $zoneZip = Invoke-VWorldDownload -DsId $zone.DsId -PageDsId "24" -FileNo $zone.FileNo -DownloadDir $zoneDownloadDir -ProfileDir $profileDir -UseMultiDownload -VWorldId $VWorldId -VWorldPassword $VWorldPassword
    Write-Host "용도지역 ZIP 다운로드 완료: $($zoneZip.FullName)"
    $zoneZipArgs += @("--zone-zip", "$($zone.Key)=$($zoneZip.FullName)")
}
Write-Host "[4/6] QGIS에서 SHP를 읽고 입력 주소 반경 안의 필지와 용도지역을 선택하는 중입니다."
Write-Host "[5/6] 선택 필지를 CAD용 경계선으로 바꾸고 지번/용도지역 라벨을 준비하는 중입니다."
Write-Host "[6/6] 용도지역이 포함된 DXF와 QGIS 프로젝트를 저장하는 중입니다."

$processArgs = @(
    $script,
    "--address", $Address,
    "--vworld-key", $VWorldKey,
    "--radius", "$Radius",
    "--out-dir", $OutDir,
    "--input-zip", $zip.FullName
)
if ($Style) {
    $processArgs += @("--style", $Style)
}
$processArgs += $zoneZipArgs
if ($OpenQgis) {
    $processArgs += "--open-qgis"
}

& $qgisPython @processArgs
Write-Host "완료되었습니다. 결과 저장 위치: $OutDir"
