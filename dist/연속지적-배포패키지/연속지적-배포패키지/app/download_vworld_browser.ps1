param(
    [string]$DsId = "30564",
    [string]$PageDsId,
    [string]$FileNo = "491",
    [string]$FileSizeKb = "2610",
    [int]$PageIndex = 12,
    [string]$DownloadDir = "$PSScriptRoot\outputs\browser_downloads",
    [int]$RemoteDebuggingPort = 9222,
    [int]$LoginWaitSeconds = 300,
    [string]$ProfileDir,
    [int]$DownloadWaitSeconds = 300,
    [switch]$UseMultiDownload,
    [string]$VWorldId,
    [string]$VWorldPassword
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function Find-Chrome {
    $candidates = @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return $candidate
        }
    }
    throw "Chrome 또는 Edge 실행 파일을 찾지 못했습니다."
}

function Send-Cdp {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [string]$Method,
        [hashtable]$Params = @{}
    )
    $script:cdpId += 1
    $payload = @{ id = $script:cdpId; method = $Method; params = $Params } | ConvertTo-Json -Depth 20 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
    $segment = [ArraySegment[byte]]::new($bytes)
    $Socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, [Threading.CancellationToken]::None).GetAwaiter().GetResult()

    while ($true) {
        $buffer = New-Object byte[] 1048576
        $receiveSegment = [ArraySegment[byte]]::new($buffer)
        $result = $Socket.ReceiveAsync($receiveSegment, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
        $text = [System.Text.Encoding]::UTF8.GetString($buffer, 0, $result.Count)
        if (-not $text) {
            continue
        }
        $json = $text | ConvertFrom-Json
        if ($json.id -eq $script:cdpId) {
            if ($json.error) {
                throw "$Method 실패: $($json.error.message)"
            }
            return $json.result
        }
    }
}

function Invoke-Js {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [string]$Expression
    )
    return Send-Cdp -Socket $Socket -Method "Runtime.evaluate" -Params @{
        expression = $Expression
        awaitPromise = $true
        returnByValue = $true
    }
}

$chrome = Find-Chrome
if (-not $PageDsId) {
    $PageDsId = $DsId
}
$downloadPath = (Resolve-Path -LiteralPath (New-Item -ItemType Directory -Force -Path $DownloadDir)).Path
$profilePath = $ProfileDir
if (-not $profilePath) {
    $profilePath = Join-Path $DownloadDir "..\chrome_vworld_profile"
}
New-Item -ItemType Directory -Force -Path $profilePath | Out-Null

$detailUrl = "https://www.vworld.kr/dtmk/dtmk_ntads_s002.do?svcCde=MK&dsId=$PageDsId&pageIndex=$PageIndex"
$chromeArgs = @(
    "--remote-debugging-port=$RemoteDebuggingPort",
    "--user-data-dir=$profilePath",
    "--no-first-run",
    "--disable-popup-blocking",
    "--disable-features=DownloadBubble",
    $detailUrl
)
Start-Process -FilePath $chrome -ArgumentList $chromeArgs | Out-Null

$versionUrl = "http://127.0.0.1:$RemoteDebuggingPort/json/version"
$tabsUrl = "http://127.0.0.1:$RemoteDebuggingPort/json"
$deadline = (Get-Date).AddSeconds(30)
do {
    try {
        Invoke-RestMethod -Uri $versionUrl -TimeoutSec 2 | Out-Null
        break
    } catch {
        Start-Sleep -Milliseconds 500
    }
} while ((Get-Date) -lt $deadline)

$tabs = Invoke-RestMethod -Uri $tabsUrl
$tab = $tabs | Where-Object { $_.url -like "*dtmk_ntads_s002.do*" -and $_.url -like "*dsId=$PageDsId*" } | Select-Object -First 1
if (-not $tab) {
    $tab = $tabs | Where-Object { $_.url -like "*dtmk_ntads_s002.do*" } | Sort-Object id -Descending | Select-Object -First 1
}
if (-not $tab) {
    throw "VWorld 탭을 찾지 못했습니다. pageDsId=$PageDsId downloadDsId=$DsId"
}

$socket = [System.Net.WebSockets.ClientWebSocket]::new()
$socket.ConnectAsync([Uri]$tab.webSocketDebuggerUrl, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
$script:cdpId = 0

Send-Cdp -Socket $socket -Method "Page.enable" | Out-Null
Send-Cdp -Socket $socket -Method "Runtime.enable" | Out-Null
try {
    Send-Cdp -Socket $socket -Method "Browser.setDownloadBehavior" -Params @{
        behavior = "allow"
        downloadPath = $downloadPath
    } | Out-Null
} catch {
    Send-Cdp -Socket $socket -Method "Page.setDownloadBehavior" -Params @{
        behavior = "allow"
        downloadPath = $downloadPath
    } | Out-Null
}

Write-Host "VWorld 다운로드 페이지를 열었습니다."
Write-Host "로그인 상태를 확인하는 중입니다."

$login = Invoke-Js -Socket $socket -Expression "window.menuFnc && menuFnc.loginYn ? menuFnc.loginYn : 'UNKNOWN'"
$loginValue = $login.result.value
if (($loginValue -eq "N" -or $loginValue -eq "UNKNOWN") -and $VWorldId -and $VWorldPassword) {
    Write-Host "입력한 VWorld 아이디/비밀번호로 자동 로그인을 시도합니다."
    $idJson = ConvertTo-Json $VWorldId -Compress
    $pwJson = ConvertTo-Json $VWorldPassword -Compress
    Invoke-Js -Socket $socket -Expression @"
(async () => {
  const body = new URLSearchParams();
  body.set('usrIdeE', btoa($idJson));
  body.set('usrPwdE', btoa($pwJson));
  body.set('nextUrl', window.location.pathname + window.location.search);
  const res = await fetch('/v4po_usrlogin_a004.do', {
    method: 'POST',
    credentials: 'same-origin',
    headers: {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8'},
    body
  });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch (e) { return {status: 'LOGIN_NON_JSON', text: text.slice(0, 500)}; }
  if (data.resultMap && data.resultMap.result === 'success') {
    const nextUrl = data.resultMap.nextUrl || (window.location.pathname + window.location.search);
    window.location.href = nextUrl;
    return {status: 'LOGIN_SUCCESS', nextUrl};
  }
  if (data.resultMap && data.resultMap.url) {
    return {status: data.resultMap.result || 'LOGIN_REDIRECT_REQUIRED', msg: data.resultMap.msg, url: data.resultMap.url};
  }
  return {status: data.resultMap ? data.resultMap.result : 'LOGIN_UNKNOWN', msg: data.resultMap ? data.resultMap.msg : text.slice(0, 500)};
})()
"@ | Out-Null
    Start-Sleep -Seconds 2
}

$loginDeadline = (Get-Date).AddSeconds($LoginWaitSeconds)
do {
    $login = Invoke-Js -Socket $socket -Expression "window.menuFnc && menuFnc.loginYn ? menuFnc.loginYn : 'UNKNOWN'"
    $loginValue = $login.result.value
    if ($loginValue -ne "N" -and $loginValue -ne "UNKNOWN") {
        break
    }
    Write-Host "VWorld 로그인을 기다리는 중입니다."
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $loginDeadline)

if ($loginValue -eq "N" -or $loginValue -eq "UNKNOWN") {
    throw "로그인이 확인되지 않았습니다. 브라우저에서 로그인한 뒤 다시 실행해주세요."
}

Write-Host "VWorld 로그인 확인 완료. ZIP 다운로드를 시작합니다."
$before = Get-ChildItem -LiteralPath $downloadPath -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
if ($UseMultiDownload) {
    $downloadPathUrl = "/dtmk/downloadResourceFile2.do?ds_id=$DsId&ds_file_sq=$FileNo"
} else {
    $downloadPathUrl = "/dtmk/downloadResourceFile.do?ds_id=$DsId&fileNo=$FileNo"
}
$downloadPathJson = ConvertTo-Json $downloadPathUrl -Compress
Invoke-Js -Socket $socket -Expression @"
(() => {
  const href = $downloadPathJson;
  const a = document.createElement('a');
  a.href = href;
  a.download = '';
  document.body.appendChild(a);
  a.click();
  a.remove();
  return href;
})()
"@ | Out-Null

$downloadDeadline = (Get-Date).AddSeconds($DownloadWaitSeconds)
do {
    $files = Get-ChildItem -LiteralPath $downloadPath -File -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -notin $before -and $_.Name -notlike "*.crdownload" -and $_.Length -gt 0 } |
        Sort-Object LastWriteTime -Descending
    if ($files) {
        $file = $files | Select-Object -First 1
        Write-Host "다운로드 완료: $($file.FullName)"
        $socket.Dispose()
        exit 0
    }
    Write-Host "ZIP 파일 다운로드가 끝나기를 기다리는 중입니다."
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $downloadDeadline)

$socket.Dispose()
throw "다운로드 파일을 확인하지 못했습니다. 열린 브라우저의 알림/다운로드 상태를 확인해주세요. dsId=$DsId fileNo=$FileNo downloadDir=$downloadPath"
