param(
    [Parameter(Mandatory = $true)]
    [string]$Address,

    [Parameter(Mandatory = $true)]
    [string]$VWorldKey,

    [string]$VWorldId,
    [string]$VWorldPassword,
    [double]$Radius = 5000,
    [string]$OutDir,
    [string[]]$ParcelListPath,
    [string]$Style,
    [string]$VWorldDomain = $env:VWORLD_DOMAIN,
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
        [string]$FileSizeKb = "1",
        [string]$DownloadHref,
        [string]$ExpectedFileName,
        [string]$DownloadDir,
        [string]$ProfileDir,
        [string]$VWorldId,
        [string]$VWorldPassword
    )

    function Test-ZipFile {
        param([string]$Path)
        if (-not (Test-Path -LiteralPath $Path)) {
            return $false
        }
        $file = Get-Item -LiteralPath $Path
        if ($file.Length -le 512) {
            return $false
        }
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $bytes = New-Object byte[] 4
            $read = $stream.Read($bytes, 0, 4)
            return ($read -eq 4 -and $bytes[0] -eq 0x50 -and $bytes[1] -eq 0x4B)
        } finally {
            $stream.Dispose()
        }
    }

    New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
    if ($ExpectedFileName) {
        $cachedZip = Get-ChildItem -LiteralPath $DownloadDir -Filter $ExpectedFileName -File -ErrorAction SilentlyContinue |
            Where-Object { Test-ZipFile -Path $_.FullName } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($cachedZip) {
            Write-Host "기존 ZIP 재사용: $($cachedZip.FullName)"
            return $cachedZip
        } else {
            Get-ChildItem -LiteralPath $DownloadDir -Filter $ExpectedFileName -File -ErrorAction SilentlyContinue |
                Where-Object { -not (Test-ZipFile -Path $_.FullName) } |
                ForEach-Object {
                    Write-Host "손상/비정상 ZIP 삭제: $($_.FullName)"
                    Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
                }
        }
    }

    $downloadStart = Get-Date
    $before = Get-ChildItem -LiteralPath $DownloadDir -Filter "*.zip" -File -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty FullName
    $waitSeconds = "300"
    if ($FileSizeKb -and ([int64]$FileSizeKb) -gt 512000) {
        $waitSeconds = "1800"
    }
    $downloadArgs = @(
        "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $PSScriptRoot "download_vworld_browser.ps1"),
        "-DsId", $DsId,
        "-FileNo", $FileNo,
        "-FileSizeKb", $FileSizeKb,
        "-DownloadDir", $DownloadDir,
        "-ProfileDir", $ProfileDir,
        "-DownloadWaitSeconds", $waitSeconds
    )
    if ($ExpectedFileName) {
        $downloadArgs += @("-ExpectedFileName", $ExpectedFileName)
    }
    if ($PageDsId) {
        $downloadArgs += @("-PageDsId", $PageDsId)
    }
    if ($DownloadHref) {
        $downloadArgs += @("-DownloadHref", $DownloadHref)
    }
    if ($VWorldId -and $VWorldPassword) {
        $downloadArgs += @("-VWorldId", $VWorldId, "-VWorldPassword", $VWorldPassword)
    }

    & powershell @downloadArgs
    $downloadExitCode = $LASTEXITCODE
    if ($downloadExitCode -ne 0) {
        if ($ExpectedFileName) {
            $cachedZip = Get-ChildItem -LiteralPath $DownloadDir -Filter $ExpectedFileName -File -ErrorAction SilentlyContinue |
                Where-Object { Test-ZipFile -Path $_.FullName } |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($cachedZip) {
                Write-Host "다운로드 실패, 정확히 일치하는 기존 ZIP 재사용: $($cachedZip.FullName)"
                return $cachedZip
            }
        }
        throw "VWorld ZIP 다운로드 스크립트가 실패했습니다. dsId=$DsId fileNo=$FileNo downloadDir=$DownloadDir"
    }

    $zip = Get-ChildItem -LiteralPath $DownloadDir -Filter "*.zip" -File -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.FullName -notin $before -or $_.LastWriteTime -ge $downloadStart) -and
            (-not $ExpectedFileName -or $_.Name -eq $ExpectedFileName) -and
            (Test-ZipFile -Path $_.FullName)
        } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $zip -and $ExpectedFileName) {
        $zip = Get-ChildItem -LiteralPath $DownloadDir -Filter $ExpectedFileName -File -ErrorAction SilentlyContinue |
            Where-Object { Test-ZipFile -Path $_.FullName } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($zip) {
            Write-Host "새 다운로드 확인 실패, 정확히 일치하는 기존 ZIP 재사용: $($zip.FullName)"
        }
    }
    if (-not $zip) {
        throw "새로 다운로드된 ZIP을 찾지 못했습니다. 기존 ZIP은 재사용하지 않습니다: $DownloadDir"
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

# 네이티브(python-qgis.bat)가 stderr(트레이스백)를 쓰면 ErrorActionPreference=Stop이
# 첫 줄에서 스크립트를 죽여 실제 에러가 가려진다. Continue로 낮춰 전체 출력을 캡처한 뒤
# 우리가 직접 진단 메시지를 던진다.
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$dryRunText = & $qgisPython @dryArgs 2>&1
$dryExit = $LASTEXITCODE
$ErrorActionPreference = $prevEAP
$dryRunRaw = ($dryRunText | ForEach-Object { $_.ToString() }) -join "`n"
$jsonStart = $dryRunRaw.IndexOf("{")
$jsonEnd = $dryRunRaw.LastIndexOf("}")
# 실패 판정은 종료코드 우선(에러 트레이스백의 파이썬 dict에도 중괄호가 있어 중괄호 유무로는 부족)
if ($dryExit -ne 0 -or $jsonStart -lt 0 -or $jsonEnd -le $jsonStart) {
    if ($dryRunRaw -match "INVALID_KEY|등록되지 않은 인증") {
        throw "VWorld 지오코딩 인증 실패(INVALID_KEY). 입력한 VWorld API 키가 '지오코더(주소→좌표) API'에 등록·승인된 키인지 확인하세요. 웹 위치도는 되는데 여기서만 실패하면, 정식 연속지적에 넘어간 키가 비어있거나 다른 키일 수 있습니다.`n`n$dryRunRaw"
    }
    if ($dryRunRaw -match "NOT_FOUND") {
        throw "주소 지오코딩 실패. 시/군/구를 포함한 전체 주소로 입력하세요. 예: '서울특별시 강남구 논현동 151-27번지'.`n`n$dryRunRaw"
    }
    throw "Dry-run이 리소스 매칭 전에 실패했습니다(종료코드 $dryExit).`n`n$dryRunRaw"
}
$dryRunJson = $dryRunRaw.Substring($jsonStart, $jsonEnd - $jsonStart + 1)
$dryRun = $dryRunJson | ConvertFrom-Json
$fileNoMatch = [regex]::Match($dryRun.download_href, 'fileNo=(\d+)')
if (-not $fileNoMatch.Success) {
    throw "Could not read fileNo from download href: $($dryRun.download_href)"
}
$fileNo = $fileNoMatch.Groups[1].Value
$downloadHref = [string]$dryRun.download_href
$expectedZipName = ([string]$dryRun.resource) -replace "\s+데이터\s+SHP$", ""

Write-Host "주소 확인 완료: X=$($dryRun.x), Y=$($dryRun.y)"
Write-Host "다운로드 대상: $($dryRun.resource)"
Write-Host "다운로드 URL: $downloadHref"
if ($VWorldId -and $VWorldPassword) {
    Write-Host "VWorld 계정 정보 전달 상태: 입력됨"
} else {
    Write-Host "VWorld 계정 정보 전달 상태: 비어 있음"
}
Write-Host "[2/6] VWorld 브라우저를 열고 연속지적 ZIP을 다운로드하는 중입니다."

$downloadDir = Join-Path $OutDir "browser_downloads"
$profileDir = Join-Path $OutDir "chrome_vworld_profile"
$zip = Invoke-VWorldDownload -DsId "30564" -FileNo $fileNo -DownloadHref $downloadHref -ExpectedFileName $expectedZipName -DownloadDir $downloadDir -ProfileDir $profileDir -VWorldId $VWorldId -VWorldPassword $VWorldPassword
Write-Host "ZIP 다운로드 완료: $($zip.FullName)"
Write-Host "[3/6] VWorld 용도지역 ZIP 4종을 다운로드하는 중입니다."

$zoneSpecs = @(
    @{ Key = "urban"; Label = "도시지역"; DsId = "20171128DS00117"; FileNo = "1527"; SizeKb = "129272"; FileName = "AL_D124_00_20260309.zip" },
    @{ Key = "management"; Label = "관리지역"; DsId = "20171128DS00118"; FileNo = "1534"; SizeKb = "586341"; FileName = "AL_D125_00_20260309.zip" },
    @{ Key = "agriculture"; Label = "농림지역"; DsId = "20171128DS00119"; FileNo = "1459"; SizeKb = "334732"; FileName = "AL_D126_00_20260309.zip" },
    @{ Key = "nature"; Label = "자연환경보전지역"; DsId = "20171128DS00120"; FileNo = "449"; SizeKb = "14003"; FileName = "AL_D127_00_20260309.zip" }
)
$zoneZipArgs = @()
foreach ($zone in $zoneSpecs) {
    Write-Host "용도지역 다운로드: $($zone.Label)"
    $zoneDownloadDir = Join-Path $OutDir ("browser_downloads\zones\" + $zone.Key)
    $zoneHref = "https://www.vworld.kr/dtmk/downloadResourceFile.do?ds_id=$($zone.DsId)&fileNo=$($zone.FileNo)"
    $zoneZip = Invoke-VWorldDownload -DsId $zone.DsId -PageDsId $zone.DsId -FileNo $zone.FileNo -FileSizeKb $zone.SizeKb -DownloadHref $zoneHref -ExpectedFileName $zone.FileName -DownloadDir $zoneDownloadDir -ProfileDir $profileDir -VWorldId $VWorldId -VWorldPassword $VWorldPassword
    Write-Host "용도지역 ZIP 다운로드 완료: $($zoneZip.FullName)"
    $zoneZipArgs += @("--zone-zip", "$($zone.Key)=$($zoneZip.FullName)")
}

# 도로명주소 실폭도로(시도별 SHP, Z_KAIS_TL_SPRD_RW) — dry-run이 찾아준 리소스를 다운로드
$roadZipArgs = @()
if ($dryRun.road_download_href) {
    $roadFileNoMatch = [regex]::Match([string]$dryRun.road_download_href, 'fileNo=(\d+)')
    if ($roadFileNoMatch.Success) {
        $roadFileNo = $roadFileNoMatch.Groups[1].Value
        $roadExpected = ([string]$dryRun.road_resource) -replace "\s+데이터\s+SHP$", ""
        Write-Host "[3.5] 도로명주소 실폭도로 ZIP을 다운로드하는 중입니다: $($dryRun.road_resource)"
        $roadDownloadDir = Join-Path $OutDir "browser_downloads\silpok"
        try {
            $roadZip = Invoke-VWorldDownload -DsId "30057" -PageDsId "30057" -FileNo $roadFileNo -DownloadHref ([string]$dryRun.road_download_href) -ExpectedFileName $roadExpected -DownloadDir $roadDownloadDir -ProfileDir $profileDir -VWorldId $VWorldId -VWorldPassword $VWorldPassword
            Write-Host "실폭도로 ZIP 다운로드 완료: $($roadZip.FullName)"
            $roadZipArgs += @("--road-zip", $roadZip.FullName)
        } catch {
            Write-Host "실폭도로 다운로드를 건너뜁니다(계속 진행): $($_.Exception.Message)"
        }
    }
} else {
    Write-Host "실폭도로 리소스를 찾지 못해 건너뜁니다(시도 매칭 실패 가능)."
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
if ($ParcelListPath) {
    foreach ($parcelList in $ParcelListPath) {
        if ($parcelList) {
            $processArgs += @("--parcel-list", $parcelList)
        }
    }
}
if ($Style) {
    $processArgs += @("--style", $Style)
}
$processArgs += $zoneZipArgs
$processArgs += $roadZipArgs
if ($VWorldDomain) {
    $processArgs += @("--vworld-domain", $VWorldDomain)
}

& $qgisPython @processArgs
Write-Host "완료되었습니다. 결과 저장 위치: $OutDir"
