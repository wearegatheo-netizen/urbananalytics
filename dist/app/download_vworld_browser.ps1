param(
    [string]$DsId = "30564",
    [string]$PageDsId,
    [string]$FileNo = "491",
    [string]$DownloadHref,
    [string]$FileSizeKb = "2610",
    [int]$PageIndex = 12,
    [string]$DownloadDir = "$PSScriptRoot\outputs\browser_downloads",
    [int]$RemoteDebuggingPort = 9222,
    [int]$LoginWaitSeconds = 300,
    [string]$ProfileDir,
    [int]$DownloadWaitSeconds = 300,
    [string]$ExpectedFileName,
    [string]$VWorldId,
    [string]$VWorldPassword
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Net.Http

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
        $message = New-Object System.Collections.Generic.List[byte]
        do {
            $buffer = New-Object byte[] 1048576
            $receiveSegment = [ArraySegment[byte]]::new($buffer)
            $result = $Socket.ReceiveAsync($receiveSegment, [Threading.CancellationToken]::None).GetAwaiter().GetResult()
            if ($result.Count -gt 0) {
                $message.AddRange([byte[]]($buffer[0..($result.Count - 1)]))
            }
        } while (-not $result.EndOfMessage)
        $text = [System.Text.Encoding]::UTF8.GetString($message.ToArray())
        if (-not $text) {
            continue
        }
        $json = $text | ConvertFrom-Json
        if ($json.id -eq $script:cdpId) {
            if ($json.error) {
                throw "$Method 실패: $($json.error.message)"
            }
            if ($json.result -and $json.result.exceptionDetails) {
                $detail = $json.result.exceptionDetails
                $message = $detail.exception.description
                if (-not $message) {
                    $message = $detail.text
                }
                throw "$Method 실행 예외: $message"
            }
            return $json.result
        }
    }
}

function Get-RuntimeValue {
    param($Result)
    if ($Result -and $Result.result) {
        return $Result.result.value
    }
    return $null
}

function Save-Base64File {
    param(
        [string]$Base64,
        [string]$Path
    )
    $bytes = [Convert]::FromBase64String($Base64)
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function Save-BrowserFetchedFile {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [string]$Url,
        [string]$OutFile
    )
    $urlJson = ConvertTo-Json $Url -Compress
    $fetchResult = Invoke-Js -Socket $Socket -Expression @"
(async () => {
  const url = new URL($urlJson, 'https://www.vworld.kr').href;
  const res = await fetch(url, {
    credentials: 'include',
    cache: 'no-store',
    headers: {'Accept': 'application/zip,application/octet-stream,*/*'}
  });
  const contentType = res.headers.get('content-type') || '';
  const disposition = res.headers.get('content-disposition') || '';
  const buffer = await res.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  const head = Array.from(bytes.slice(0, 4));
  if (!res.ok || bytes.length <= 512 || head[0] !== 80 || head[1] !== 75) {
    const decoder = new TextDecoder('utf-8', {fatal: false});
    return {
      ok: false,
      status: res.status,
      contentType,
      disposition,
      size: bytes.length,
      head,
      text: decoder.decode(bytes.slice(0, 800))
    };
  }
  let binary = '';
  const chunkSize = 32768;
  for (let i = 0; i < bytes.length; i += chunkSize) {
    binary += String.fromCharCode.apply(null, bytes.slice(i, i + chunkSize));
  }
  return {
    ok: true,
    status: res.status,
    contentType,
    disposition,
    size: bytes.length,
    base64: btoa(binary)
  };
})()
"@
    $value = Get-RuntimeValue $fetchResult
    if ($value -and $value.ok -and $value.base64) {
        Save-Base64File -Base64 ([string]$value.base64) -Path $OutFile
    }
    return $value
}

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

function Download-FileWithCookies {
    param(
        [string]$Url,
        [string]$OutFile,
        [string]$CookieHeader,
        [string]$Referer
    )
    $handler = [System.Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $true
    $client = [System.Net.Http.HttpClient]::new($handler)
    try {
        $client.DefaultRequestHeaders.UserAgent.ParseAdd("Mozilla/5.0")
        $client.DefaultRequestHeaders.Accept.ParseAdd("application/zip,application/octet-stream,*/*")
        if ($Referer) {
            $client.DefaultRequestHeaders.Referrer = [Uri]$Referer
        }
        if ($CookieHeader) {
            $client.DefaultRequestHeaders.Add("Cookie", $CookieHeader)
        }
        $response = $client.GetAsync($Url, [System.Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
        $response.EnsureSuccessStatusCode()
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $fileStream = [System.IO.File]::Open($OutFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $stream.CopyTo($fileStream)
        } finally {
            $fileStream.Dispose()
            $stream.Dispose()
        }
    } finally {
        $client.Dispose()
        $handler.Dispose()
    }
}

function Get-BrowserCookieHeader {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket
    )
    try {
        $cookieResult = Send-Cdp -Socket $Socket -Method "Network.getCookies" -Params @{
            urls = @("https://www.vworld.kr", "https://www.vworld.kr/")
        }
        if ($cookieResult -and $cookieResult.cookies) {
            $pairs = @()
            foreach ($cookie in $cookieResult.cookies) {
                if ($cookie.name) {
                    $pairs += ("{0}={1}" -f $cookie.name, $cookie.value)
                }
            }
            if ($pairs.Count -gt 0) {
                return ($pairs -join "; ")
            }
        }
    } catch {
        Write-Output "브라우저 전체 쿠키 확인 실패, 일반 쿠키로 재시도합니다: $($_.Exception.Message)"
    }

    $cookieInfo = Invoke-Js -Socket $Socket -Expression "document.cookie"
    return [string](Get-RuntimeValue $cookieInfo)
}

function Find-VWorldDownloadInfo {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [string]$DatasetId,
        [string]$TargetFileNo,
        [int]$MaxPages = 40
    )

    Send-Cdp -Socket $Socket -Method "Page.enable" | Out-Null
    for ($page = 1; $page -le $MaxPages; $page++) {
        $pageUrl = "https://www.vworld.kr/dtmk/dtmk_ntads_s002.do?svcCde=MK&dsId=$DatasetId&pageIndex=$page"
        Send-Cdp -Socket $Socket -Method "Page.navigate" -Params @{ url = $pageUrl } | Out-Null
        Start-Sleep -Milliseconds 1200
        $fileNoJson = ConvertTo-Json $TargetFileNo -Compress
        $info = Invoke-Js -Socket $Socket -Expression @"
(() => {
  const target = $fileNoJson;
  for (const button of [...document.querySelectorAll('button')]) {
    const onclick = String(button.getAttribute('onclick') || '');
    const match = onclick.match(/listFnc\.download\('([^']+)',\s*'([^']+)',\s*'([^']+)'/);
    if (match && match[2] === target) {
      const row = button.closest('li, tr, div')?.parentElement || button.closest('li, tr, div') || button.parentElement;
      return {
        found: true,
        page: $page,
        dsId: match[1],
        fileNo: match[2],
        sizeKb: match[3],
        text: (row?.innerText || '').replace(/\s+/g, ' ').trim(),
        url: location.href
      };
    }
  }
  return { found: false, page: $page, url: location.href };
})()
"@
        $value = Get-RuntimeValue $info
        if ($value -and $value.found) {
            return $value
        }
    }
    return $null
}

function Get-RaonkDownloadInfo {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [string]$DatasetId,
        [string]$TargetFileNo
    )
    $dsIdJson = ConvertTo-Json $DatasetId -Compress
    $fileNoJson = ConvertTo-Json $TargetFileNo -Compress
    $info = Invoke-Js -Socket $Socket -Expression @"
(async () => {
  const dsId = $dsIdJson;
  const fileNo = $fileNoJson;
  const infoUrl = new URL('/dtmk/downloadResourceFile2.do?ds_id=' + encodeURIComponent(dsId) + '&ds_file_sq=' + encodeURIComponent(fileNo), 'https://www.vworld.kr').href;
  const res = await fetch(infoUrl, {
    credentials: 'include',
    cache: 'no-store'
  });
  const text = await res.text();
  const match = text.match(/RAONKUPLOAD\.AddUploadedFile\('[^']*',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)'/);
  if (!match) {
    return {ok: false, status: res.status, text: text.slice(0, 800)};
  }
  return {
    ok: true,
    status: res.status,
    fileName: match[1],
    uploadPath: match[2],
    href: new URL(match[2], 'https://www.vworld.kr').href,
    byteSize: match[3],
    key: match[4]
  };
})()
"@
    return Get-RuntimeValue $info
}

function Wait-ForZipFile {
    param(
        [string]$Directory,
        [string]$ExpectedFileName,
        [datetime]$Since,
        [int]$TimeoutSeconds = 300
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $files = Get-ChildItem -LiteralPath $Directory -File -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -notlike "*.crdownload" -and
                $_.Extension -ieq ".zip" -and
                $_.LastWriteTime -ge $Since -and
                (-not $ExpectedFileName -or $_.Name -eq $ExpectedFileName) -and
                (Test-ZipFile -Path $_.FullName)
            } |
            Sort-Object LastWriteTime -Descending
        if ($files) {
            return ($files | Select-Object -First 1)
        }
        Start-Sleep -Seconds 1
    } while ((Get-Date) -lt $deadline)
    return $null
}

function Invoke-RaonkAgentDownload {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        $RaonkInfo,
        [string]$DatasetId,
        [string]$FileNo,
        [string]$DownloadPath,
        [int]$TimeoutSeconds = 300
    )
    $pageUrl = "https://www.vworld.kr/dtmk/downloadResourceFile2.do?ds_id=$DatasetId&ds_file_sq=$FileNo"
    Send-Cdp -Socket $Socket -Method "Page.navigate" -Params @{ url = $pageUrl } | Out-Null
    Start-Sleep -Seconds 3
    Install-VWorldAutomationHooks -Socket $Socket

    $nameJson = ConvertTo-Json ([string]$RaonkInfo.fileName) -Compress
    $pathJson = ConvertTo-Json ([string]$RaonkInfo.uploadPath) -Compress
    $sizeJson = ConvertTo-Json ([string]$RaonkInfo.byteSize) -Compress
    $keyJson = ConvertTo-Json ([string]$RaonkInfo.key) -Compress
    $dirJson = ConvertTo-Json $DownloadPath -Compress

    $kick = Invoke-Js -Socket $Socket -Expression @"
(async () => {
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  for (let i = 0; i < 80; i++) {
    if (window.RAONKUPLOAD && window.G_UploadID) break;
    await sleep(500);
  }
  if (!window.RAONKUPLOAD || !window.G_UploadID) {
    return {ok: false, reason: 'RAONK_NOT_READY', uploadID: window.G_UploadID || null};
  }
  try {
    if (typeof RAONKUPLOAD.SetDefaultDownloadPath === 'function') {
      RAONKUPLOAD.SetDefaultDownloadPath($dirJson, G_UploadID);
    }
    RAONKUPLOAD.ResetUpload(G_UploadID);
    RAONKUPLOAD.AddUploadedFile('1', $nameJson, $pathJson, $sizeJson, $keyJson, G_UploadID);
    RAONKUPLOAD.DownloadAllFile(G_UploadID);
    return {ok: true, uploadID: G_UploadID, fileName: $nameJson, dir: $dirJson};
  } catch (e) {
    return {ok: false, reason: 'RAONK_CALL_FAILED', message: String(e && (e.stack || e.message || e))};
  }
})()
"@
    $kickValue = Get-RuntimeValue $kick
    Write-Output ("RAONK 에이전트 다운로드 호출: " + (($kickValue | ConvertTo-Json -Compress -Depth 8)))
    if (-not ($kickValue -and $kickValue.ok)) {
        return $null
    }
    return Wait-ForZipFile -Directory $DownloadPath -ExpectedFileName ([string]$RaonkInfo.fileName) -Since (Get-Date).AddSeconds(-5) -TimeoutSeconds $TimeoutSeconds
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

function Close-VWorldAutomationPages {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket,
        [int]$Port
    )
    try {
        if ($Socket) {
            Send-Cdp -Socket $Socket -Method "Page.close" | Out-Null
        }
    } catch {
    }
    try {
        $tabs = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json" -TimeoutSec 2
        foreach ($tabItem in @($tabs)) {
            $url = [string]$tabItem.url
            $title = [string]$tabItem.title
            $isDownloadPage =
                $url -like "*vworld.kr/dtmk/dtmk_ntads_s002.do*" -or
                $url -like "*vworld.kr/dtmk/downloadResourceFile2.do*" -or
                $title -like "*브이월드 공간정보 다운로드*" -or
                $title -like "*데이터셋 파일업로드*"
            if ($isDownloadPage -and $tabItem.id) {
                Invoke-RestMethod -Uri "http://127.0.0.1:$Port/json/close/$($tabItem.id)" -TimeoutSec 2 | Out-Null
            }
        }
    } catch {
    }
    try {
        if ($Socket) {
            $Socket.Dispose()
        }
    } catch {
    }
}

function Install-VWorldAutomationHooks {
    param(
        [System.Net.WebSockets.ClientWebSocket]$Socket
    )
    Invoke-Js -Socket $Socket -Expression @"
(() => {
  window.__codexVworldMessages = window.__codexVworldMessages || [];
  window.alert = function(message) {
    window.__codexVworldMessages.push({type: 'alert', message: String(message || '')});
    return true;
  };
  window.confirm = function(message) {
    window.__codexVworldMessages.push({type: 'confirm', message: String(message || '')});
    return true;
  };
  const installMsgHook = () => {
    if (!window.msgFnc || window.msgFnc.__codexHooked) return;
    const originalAlert = typeof window.msgFnc.alert === 'function' ? window.msgFnc.alert.bind(window.msgFnc) : null;
    window.msgFnc.alert = function(options) {
      const text = typeof options === 'string' ? options : ((options && (options.text || options.title)) || '');
      window.__codexVworldMessages.push({type: 'msgFnc.alert', message: String(text || '')});
      if (options && typeof options.callBackConfirmFnc === 'function') {
        try { options.callBackConfirmFnc(); } catch (e) {}
      }
      setTimeout(() => {
        for (const el of [...document.querySelectorAll('.popup, #mask, .dim, .modal')]) {
          const body = el.innerText || '';
          if (/로그인 후 이용|로그인/.test(body)) el.remove();
        }
      }, 0);
      return false;
    };
    window.msgFnc.__codexHooked = true;
    window.msgFnc.__codexOriginalAlert = originalAlert;
  };
  installMsgHook();
  const timer = setInterval(installMsgHook, 300);
  setTimeout(() => clearInterval(timer), 10000);
  return true;
})()
"@ | Out-Null
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

# 다운로드 시 '다른 이름으로 저장' 대화상자 방지: Chrome 프로필 Preferences에 자동저장 설정을 심는다.
# (CDP setDownloadBehavior가 새 타깃/탭에서 안 먹는 경우까지 커버하는 프로필 레벨 설정)
try {
    $prefDir = Join-Path $profilePath "Default"
    New-Item -ItemType Directory -Force -Path $prefDir | Out-Null
    $prefFile = Join-Path $prefDir "Preferences"
    $prefObj = $null
    if (Test-Path -LiteralPath $prefFile) {
        try { $prefObj = (Get-Content -LiteralPath $prefFile -Raw -Encoding UTF8) | ConvertFrom-Json } catch { $prefObj = $null }
    }
    if (-not $prefObj) { $prefObj = New-Object psobject }
    if (-not ($prefObj.PSObject.Properties.Name -contains 'download')) {
        $prefObj | Add-Member -NotePropertyName 'download' -NotePropertyValue (New-Object psobject) -Force
    }
    $prefObj.download | Add-Member -NotePropertyName 'prompt_for_download' -NotePropertyValue $false -Force
    $prefObj.download | Add-Member -NotePropertyName 'default_directory' -NotePropertyValue $downloadPath -Force
    $prefObj.download | Add-Member -NotePropertyName 'directory_upgrade' -NotePropertyValue $true -Force
    if (-not ($prefObj.PSObject.Properties.Name -contains 'savefile')) {
        $prefObj | Add-Member -NotePropertyName 'savefile' -NotePropertyValue (New-Object psobject) -Force
    }
    $prefObj.savefile | Add-Member -NotePropertyName 'default_directory' -NotePropertyValue $downloadPath -Force
    $prefJson = $prefObj | ConvertTo-Json -Depth 60 -Compress
    # Chrome Preferences는 UTF-8(BOM 없음)이어야 한다.
    [System.IO.File]::WriteAllText($prefFile, $prefJson, (New-Object System.Text.UTF8Encoding $false))
} catch {
    Write-Output "다운로드 자동저장 설정(Preferences) 적용 경고(계속 진행): $($_.Exception.Message)"
}

$downloadStart = Get-Date

$detailUrl = "https://www.vworld.kr/dtmk/dtmk_ntads_s002.do?svcCde=MK&dsId=$PageDsId&pageIndex=$PageIndex"
$chromeArgs = @(
    "--remote-debugging-port=$RemoteDebuggingPort",
    "--user-data-dir=$profilePath",
    "--no-first-run",
    "--disable-popup-blocking",
    "--disable-features=DownloadBubble",
    "--start-minimized",
    $detailUrl
)
Start-Process -FilePath $chrome -ArgumentList $chromeArgs -WindowStyle Minimized | Out-Null

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
Send-Cdp -Socket $socket -Method "Network.enable" | Out-Null
Install-VWorldAutomationHooks -Socket $socket
try {
    # eventsEnabled=true: 새 타깃/탭에서 시작되는 다운로드까지 브라우저 전역으로 자동저장
    Send-Cdp -Socket $socket -Method "Browser.setDownloadBehavior" -Params @{
        behavior = "allow"
        downloadPath = $downloadPath
        eventsEnabled = $true
    } | Out-Null
} catch {
    Send-Cdp -Socket $socket -Method "Page.setDownloadBehavior" -Params @{
        behavior = "allow"
        downloadPath = $downloadPath
    } | Out-Null
}

Write-Output "VWorld 다운로드 페이지를 열었습니다."
Write-Output "로그인 상태를 확인하는 중입니다."
if ($VWorldId -and $VWorldPassword) {
    Write-Output "VWorld 계정 정보가 전달되었습니다. 자동 로그인을 준비합니다."
} else {
    Write-Output "VWorld 계정 정보가 전달되지 않았습니다. 저장된 브라우저 로그인 상태만 사용합니다."
}

$login = Invoke-Js -Socket $socket -Expression "window.menuFnc && menuFnc.loginYn ? menuFnc.loginYn : 'UNKNOWN'"
$loginValue = Get-RuntimeValue $login
if ($VWorldId -and $VWorldPassword) {
    Write-Output "입력한 VWorld 아이디/비밀번호로 자동 로그인을 시도합니다."
    $idJson = ConvertTo-Json $VWorldId -Compress
    $pwJson = ConvertTo-Json $VWorldPassword -Compress
    try {
        $autoLogin = Invoke-Js -Socket $socket -Expression @"
(async () => {
  const id = $idJson;
  const pw = $pwJson;
  const nextUrl = '/dtmk/dtmk_ntads_s002.do?svcCde=MK&dsId=$PageDsId&pageIndex=$PageIndex';
  const payload = [
    {name: 'usrIdeE', value: btoa(id)},
    {name: 'usrPwdE', value: btoa(pw)},
    {name: 'nextUrl', value: nextUrl}
  ];
  const normalize = data => ({
    result: data && data.resultMap ? data.resultMap.result : undefined,
    msg: data && data.resultMap ? data.resultMap.msg : undefined,
    nextUrl: data && data.resultMap ? data.resultMap.nextUrl : undefined,
    url: data && data.resultMap ? data.resultMap.url : undefined
  });
  let data = null;
  if (window.$ && $.ajax) {
    data = await new Promise(resolve => {
      $.ajax({
        url: '/v4po_usrlogin_a004.do',
        data: payload,
        type: 'post',
        dataType: 'json',
        timeout: 100000,
        success: value => resolve(value),
        error: (xhr, status, error) => resolve({resultMap: {result: 'ajax_error', msg: status + ':' + error, text: (xhr && xhr.responseText || '').slice(0, 500)}})
      });
    });
  } else {
    const body = new URLSearchParams();
    for (const item of payload) body.append(item.name, item.value);
    const res = await fetch('https://www.vworld.kr/v4po_usrlogin_a004.do', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body
    });
    const text = await res.text();
    try { data = JSON.parse(text); } catch (e) { return {status: 'LOGIN_NON_JSON', text: text.slice(0, 500)}; }
  }
  const result = normalize(data);
  let check = null;
  try {
    const checkRes = await fetch('/im_usrlogincheck_a001.do', {
      method: 'POST',
      credentials: 'include',
      headers: {'X-Requested-With': 'XMLHttpRequest'}
    });
    const checkText = await checkRes.text();
    try { check = JSON.parse(checkText); } catch (e) { check = {text: checkText.slice(0, 300)}; }
  } catch (e) {
    check = {error: String(e)};
  }
  if (result.result === 'success') {
    if (window.menuFnc) menuFnc.loginYn = 'Y';
    location.href = nextUrl;
    return {status: 'LOGIN_SUCCESS', result, check};
  }
  return {status: 'LOGIN_FAILED', result, check};
})()
"@
        if ($autoLogin.result.value) {
            Write-Output ("자동 로그인 응답: " + (($autoLogin.result.value | ConvertTo-Json -Compress -Depth 8)))
        }
    } catch {
        Write-Output "직접 자동 로그인 호출 실패, 폼 로그인으로 재시도합니다: $($_.Exception.Message)"
    }
    Invoke-Js -Socket $socket -Expression "window.location.href = '$detailUrl'; true" | Out-Null
    Start-Sleep -Seconds 2
    Install-VWorldAutomationHooks -Socket $socket

    try {
        $formLogin = Invoke-Js -Socket $socket -Expression @"
(async () => {
  const id = $idJson;
  const pw = $pwJson;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const ready = async () => {
    for (let i = 0; i < 30; i++) {
      if (document.readyState === 'complete' || document.readyState === 'interactive') return;
      await sleep(250);
    }
  };
  const setValue = (el, value) => {
    const proto = Object.getPrototypeOf(el);
    const desc = Object.getOwnPropertyDescriptor(proto, 'value');
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
  };
  const loginState = () => {
    if (window.menuFnc && menuFnc.loginYn) return menuFnc.loginYn;
    const text = document.body ? document.body.innerText : '';
    if (/로그아웃|마이페이지/.test(text)) return 'Y';
    if (/로그인/.test(text)) return 'N';
    return 'UNKNOWN';
  };
  const findLoginLink = () => [...document.querySelectorAll('a,button,input[type=button],input[type=submit]')]
    .find(el => /로그인|login/i.test((el.innerText || el.value || el.title || el.getAttribute('href') || '').trim()));
  await ready();
  if (window.loginFnc && typeof loginFnc.login === 'function') {
    let idEl = document.getElementById('codexLoginUsrIde');
    let pwEl = document.getElementById('codexLoginUsrPwd');
    let chkEl = document.getElementById('codexChkUsrIde');
    if (!idEl) {
      idEl = document.createElement('input');
      idEl.id = 'codexLoginUsrIde';
      idEl.style.display = 'none';
      document.body.appendChild(idEl);
    }
    if (!pwEl) {
      pwEl = document.createElement('input');
      pwEl.id = 'codexLoginUsrPwd';
      pwEl.type = 'password';
      pwEl.style.display = 'none';
      document.body.appendChild(pwEl);
    }
    if (!chkEl) {
      chkEl = document.createElement('input');
      chkEl.id = 'codexChkUsrIde';
      chkEl.type = 'checkbox';
      chkEl.style.display = 'none';
      document.body.appendChild(chkEl);
    }
    setValue(idEl, id);
    setValue(pwEl, pw);
    chkEl.checked = false;
    loginFnc.moveMenu = '/dtmk/dtmk_ntads_s002.do?svcCde=MK&dsId=$PageDsId&pageIndex=$PageIndex';
    loginFnc.login('codexLoginUsrIde', 'codexLoginUsrPwd', 'codexChkUsrIde');
    await sleep(3500);
    if (window.menuFnc) menuFnc.loginYn = 'Y';
    location.href = '/dtmk/dtmk_ntads_s002.do?svcCde=MK&dsId=$PageDsId&pageIndex=$PageIndex';
    return {status: 'LOGIN_FNC_SUBMITTED', state: loginState()};
  }
  if (loginState() === 'Y') return {status: 'ALREADY_LOGIN'};
  const firstLink = findLoginLink();
  if (firstLink) {
    firstLink.click();
    await sleep(1800);
  }
  await ready();
  let password = [...document.querySelectorAll('input')].find(el => (el.type || '').toLowerCase() === 'password');
  if (!password) {
    const next = encodeURIComponent('/dtmk/dtmk_ntads_s002.do?svcCde=MK&dsId=$PageDsId&pageIndex=$PageIndex');
    location.href = 'https://www.vworld.kr/v4po_usrlogin_a001.do?nextUrl=' + next;
    await sleep(2500);
    await ready();
    password = [...document.querySelectorAll('input')].find(el => (el.type || '').toLowerCase() === 'password');
  }
  if (!password) return {status: 'LOGIN_FORM_NOT_FOUND', url: location.href, state: loginState()};
  const inputs = [...document.querySelectorAll('input')];
  const idInput = inputs.find(el => {
    const type = (el.type || 'text').toLowerCase();
    const key = ((el.name || '') + ' ' + (el.id || '') + ' ' + (el.placeholder || '') + ' ' + (el.title || '')).toLowerCase();
    return el !== password && type !== 'hidden' && type !== 'submit' && type !== 'button' && /(id|usr|user|login|아이디)/i.test(key);
  }) || inputs.filter(el => el !== password && ['text', 'email', ''].includes((el.type || 'text').toLowerCase())).pop();
  if (!idInput) return {status: 'LOGIN_ID_FIELD_NOT_FOUND', url: location.href};
  setValue(idInput, id);
  setValue(password, pw);
  const form = password.form || idInput.form;
  const submit = (form ? [...form.querySelectorAll('button,input[type=submit],input[type=button]')] : [...document.querySelectorAll('button,input[type=submit],input[type=button]')])
    .find(el => /로그인|login/i.test((el.innerText || el.value || el.title || '').trim()));
  if (submit) submit.click();
  else if (form) form.requestSubmit ? form.requestSubmit() : form.submit();
  else return {status: 'LOGIN_SUBMIT_NOT_FOUND', url: location.href};
  await sleep(3500);
  if (loginState() === 'Y') {
    location.href = '$detailUrl';
    return {status: 'LOGIN_FORM_SUCCESS'};
  }
  return {status: 'LOGIN_FORM_SUBMITTED', url: location.href, state: loginState(), text: (document.body ? document.body.innerText : '').slice(0, 300)};
})()
"@
        if ($formLogin.result.value) {
            Write-Output ("폼 자동 로그인 응답: " + (($formLogin.result.value | ConvertTo-Json -Compress -Depth 8)))
        }
    } catch {
        Write-Output "폼 자동 로그인 실패, 저장된 세션/직접 다운로드 확인으로 계속합니다: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 2
    Install-VWorldAutomationHooks -Socket $socket
}

$effectiveLoginWaitSeconds = $LoginWaitSeconds
if ($VWorldId -and $VWorldPassword) {
    $effectiveLoginWaitSeconds = [Math]::Min($LoginWaitSeconds, 20)
}
$loginDeadline = (Get-Date).AddSeconds($effectiveLoginWaitSeconds)
do {
    $login = Invoke-Js -Socket $socket -Expression @"
(() => {
  if (window.menuFnc && menuFnc.loginYn) return menuFnc.loginYn;
  const text = document.body ? document.body.innerText : '';
  if (/로그아웃|마이페이지/.test(text)) return 'Y';
  if (/로그인/.test(text)) return 'N';
  return 'UNKNOWN';
})()
"@
    $loginValue = Get-RuntimeValue $login
    if ($loginValue -ne "N" -and $loginValue -ne "UNKNOWN") {
        break
    }
    Write-Output "VWorld 로그인을 기다리는 중입니다."
    Start-Sleep -Seconds 2
} while ((Get-Date) -lt $loginDeadline)

if ($loginValue -eq "N" -or $loginValue -eq "UNKNOWN") {
    if ($VWorldId -and $VWorldPassword) {
        Write-Output "로그인 상태값은 확인되지 않았습니다. 다운로드 권한을 직접 확인합니다."
    } else {
        Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
        throw "로그인이 확인되지 않았습니다. 브라우저에서 로그인한 뒤 다시 실행해주세요."
    }
} else {
    Invoke-Js -Socket $socket -Expression "if (window.menuFnc) menuFnc.loginYn = 'Y'; true" | Out-Null
}

Write-Output "ZIP 다운로드 권한을 확인하고 파일을 저장합니다."
Install-VWorldAutomationHooks -Socket $socket
if ($DownloadHref) {
    $downloadPathUrl = $DownloadHref
} else {
    $downloadPathUrl = "/dtmk/downloadResourceFile.do?ds_id=$DsId&fileNo=$FileNo"
}
$downloadPathJson = ConvertTo-Json $downloadPathUrl -Compress
$directAttemptDebug = @()

if ($DsId -match "^20171128DS") {
    $raonkInfo = Get-RaonkDownloadInfo -Socket $socket -DatasetId $DsId -TargetFileNo $FileNo
    if (-not ($raonkInfo -and $raonkInfo.ok -and $raonkInfo.href -and $raonkInfo.fileName)) {
        $detail = if ($raonkInfo) { ($raonkInfo | ConvertTo-Json -Compress -Depth 8) } else { "NO_RAONK_INFO" }
        Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
        throw "용도지역 직접 다운로드 정보 추출 실패: dsId=$DsId fileNo=$FileNo detail=$detail"
    }
    $cookieHeader = Get-BrowserCookieHeader -Socket $socket
    $targetPath = Join-Path $downloadPath ([string]$raonkInfo.fileName)
    Write-Output "용도지역 파일 직접 저장 시작: $targetPath"
    Download-FileWithCookies -Url ([string]$raonkInfo.href) -OutFile $targetPath -CookieHeader $cookieHeader -Referer $detailUrl
    if (Test-ZipFile -Path $targetPath) {
        Write-Output "다운로드 완료: $targetPath"
        Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
        exit 0
    }
    $file = Get-Item -LiteralPath $targetPath
    Write-Output "용도지역 직접 저장 결과가 ZIP이 아니어서 RAONK 에이전트로 재시도합니다: $($file.FullName), $($file.Length) bytes"
    Remove-Item -LiteralPath $targetPath -Force -ErrorAction SilentlyContinue
    $agentZip = Invoke-RaonkAgentDownload -Socket $socket -RaonkInfo $raonkInfo -DatasetId $DsId -FileNo $FileNo -DownloadPath $downloadPath -TimeoutSeconds $DownloadWaitSeconds
    if ($agentZip) {
        Write-Output "다운로드 완료: $($agentZip.FullName)"
        Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
        exit 0
    }
    Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
    throw "용도지역 RAONK 에이전트 다운로드 결과 ZIP을 찾지 못했습니다: dsId=$DsId fileNo=$FileNo downloadDir=$downloadPath"
}

if ($DownloadHref) {
    $directName = $ExpectedFileName
    if (-not $directName) {
        $directName = "vworld_${DsId}_${FileNo}.zip"
    }
    $directPath = Join-Path $downloadPath $directName
    if (-not $FileSizeKb -or ([int64]$FileSizeKb) -le 50000) {
        try {
            Write-Output "브라우저 세션으로 VWorld 파일 직접 저장 시도: $directPath"
            $browserFetch = Save-BrowserFetchedFile -Socket $socket -Url $DownloadHref -OutFile $directPath
            if (Test-ZipFile -Path $directPath) {
                Write-Output "다운로드 완료: $directPath"
                Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
                exit 0
            }
            $fetchDebug = if ($browserFetch) { ($browserFetch | ConvertTo-Json -Compress -Depth 6) } else { "NO_FETCH_RESULT" }
            $directAttemptDebug += "browserFetch=$fetchDebug"
            Remove-Item -LiteralPath $directPath -Force -ErrorAction SilentlyContinue
            Write-Output "브라우저 세션 직접 저장 실패: $fetchDebug"
        } catch {
            $directAttemptDebug += "browserFetchException=$($_.Exception.Message)"
            Remove-Item -LiteralPath $directPath -Force -ErrorAction SilentlyContinue
            Write-Output "브라우저 세션 직접 저장 예외, 일반 직접 저장으로 재시도합니다: $($_.Exception.Message)"
        }
    }
    try {
        $cookieHeader = Get-BrowserCookieHeader -Socket $socket
        Write-Output "VWorld 파일 직접 저장 시도: $directPath"
        Download-FileWithCookies -Url $DownloadHref -OutFile $directPath -CookieHeader $cookieHeader -Referer $detailUrl
        if (Test-ZipFile -Path $directPath) {
            Write-Output "다운로드 완료: $directPath"
            Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
            exit 0
        }
        $badLength = if (Test-Path -LiteralPath $directPath) { (Get-Item -LiteralPath $directPath).Length } else { 0 }
        $directAttemptDebug += "httpClientNotZipSize=$badLength"
        Remove-Item -LiteralPath $directPath -Force -ErrorAction SilentlyContinue
        Write-Output "직접 저장 결과가 ZIP이 아니어서 브라우저 다운로드로 재시도합니다. size=$badLength"
    } catch {
        $directAttemptDebug += "httpClientException=$($_.Exception.Message)"
        Remove-Item -LiteralPath $directPath -Force -ErrorAction SilentlyContinue
        Write-Output "직접 저장 실패, 브라우저 다운로드로 재시도합니다: $($_.Exception.Message)"
    }
}

$before = Get-ChildItem -LiteralPath $downloadPath -File -ErrorAction SilentlyContinue | Select-Object -ExpandProperty FullName
$dsIdJson = ConvertTo-Json $DsId -Compress
$fileNoJson = ConvertTo-Json $FileNo -Compress
$downloadInfo = Find-VWorldDownloadInfo -Socket $socket -DatasetId $DsId -TargetFileNo $FileNo -MaxPages 40
if ($downloadInfo) {
    Write-Output ("다운로드 버튼 확인: " + (($downloadInfo | ConvertTo-Json -Compress -Depth 8)))
    $FileSizeKb = [string]$downloadInfo.sizeKb
} else {
    Write-Output "다운로드 버튼을 페이지 목록에서 찾지 못했습니다. 전달된 fileSizeKb 값으로 시도합니다."
}
if ($DsId -like "20171128DS*" -or ($FileSizeKb -and ([int64]$FileSizeKb) -gt 512000)) {
    $raonkInfo = Get-RaonkDownloadInfo -Socket $socket -DatasetId $DsId -TargetFileNo $FileNo
    Write-Output ("용도지역/대용량 파일 정보: " + (($raonkInfo | ConvertTo-Json -Compress -Depth 8)))
    if ($raonkInfo -and $raonkInfo.ok -and $raonkInfo.href -and $raonkInfo.fileName) {
        $cookieHeader = Get-BrowserCookieHeader -Socket $socket
        $targetPath = Join-Path $downloadPath ([string]$raonkInfo.fileName)
        Write-Output "용도지역/대용량 파일 직접 저장 시작: $targetPath"
        Download-FileWithCookies -Url ([string]$raonkInfo.href) -OutFile $targetPath -CookieHeader $cookieHeader -Referer $detailUrl
        if (Test-ZipFile -Path $targetPath) {
            Write-Output "다운로드 완료: $targetPath"
            Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
            exit 0
        }
        $file = Get-Item -LiteralPath $targetPath
        Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
        throw "용도지역/대용량 파일 직접 저장 결과가 비정상입니다: $($file.FullName), $($file.Length) bytes"
    }
}
$fileSizeKbJson = ConvertTo-Json $FileSizeKb -Compress
$downloadKickoff = Invoke-Js -Socket $socket -Expression @"
(() => {
  const rawHref = $downloadPathJson;
  const href = new URL(rawHref, 'https://www.vworld.kr').href;
  const targetDsId = $dsIdJson;
  const targetFileNo = $fileNoJson;
  let sizeKb = $fileSizeKbJson;
  const findDownload = () => {
    for (const button of [...document.querySelectorAll('button')]) {
      const onclick = String(button.getAttribute('onclick') || '');
      const match = onclick.match(/listFnc\.download\('([^']+)',\s*'([^']+)',\s*'([^']+)'/);
      if (match && match[1] === targetDsId && match[2] === targetFileNo) {
        return { dsId: match[1], fileNo: match[2], sizeKb: match[3], onclick };
      }
    }
    return null;
  };
  let found = findDownload();
  if (found) sizeKb = found.sizeKb;
  if (Number(sizeKb) > 512000) {
    return (async () => {
      const infoUrl = new URL('/dtmk/downloadResourceFile2.do?ds_id=' + encodeURIComponent(targetDsId) + '&ds_file_sq=' + encodeURIComponent(targetFileNo), 'https://www.vworld.kr').href;
      const res = await fetch(infoUrl, { credentials: 'include', cache: 'no-store' });
      const text = await res.text();
      const match = text.match(/RAONKUPLOAD\.AddUploadedFile\('[^']*',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)',\s*'([^']+)'/);
      if (match) {
        const directHref = new URL(match[2], 'https://www.vworld.kr').href;
        const a = document.createElement('a');
        a.href = directHref;
        a.download = match[1];
        document.body.appendChild(a);
        a.click();
        a.remove();
        return {mode: 'direct-raonk-filestore', href: directHref, fileName: match[1], byteSize: match[3], found, sizeKb: String(sizeKb || '1')};
      }
      if (window.listFnc && typeof listFnc.multiDownload === 'function') {
        for (const checkbox of [...document.querySelectorAll('input[name=chkDs]')]) {
          checkbox.checked = String(checkbox.value) === targetFileNo;
          checkbox.dispatchEvent(new Event('change', {bubbles: true}));
          checkbox.checked = String(checkbox.value) === targetFileNo;
        }
        listFnc.multiDownload(targetDsId);
        return {mode: 'listFnc.multiDownload', href, found, sizeKb: String(sizeKb || '1')};
      }
      return {mode: 'large-file-no-method', href, found, sizeKb: String(sizeKb || '1')};
    })();
  }
  if (Number(sizeKb) > 512000 && window.listFnc && typeof listFnc.multiDownload === 'function') {
    for (const checkbox of [...document.querySelectorAll('input[name=chkDs]')]) {
      checkbox.checked = String(checkbox.value) === targetFileNo;
      checkbox.dispatchEvent(new Event('change', {bubbles: true}));
      checkbox.dispatchEvent(new Event('click', {bubbles: true}));
      checkbox.checked = String(checkbox.value) === targetFileNo;
    }
    listFnc.multiDownload(targetDsId);
    return {mode: 'listFnc.multiDownload', href, found, sizeKb: String(sizeKb || '1')};
  }
  if (window.listFnc && typeof listFnc.download === 'function') {
    listFnc.download(targetDsId, targetFileNo, String(sizeKb || '1'));
    return {mode: 'listFnc.download', href, found, sizeKb: String(sizeKb || '1')};
  }
  const frameHost = document.querySelector('#downFrame') || document.body;
  const iframe = document.createElement('iframe');
  iframe.style.display = 'none';
  iframe.src = href;
  frameHost.appendChild(iframe);
  return {mode: 'iframe', href, found, sizeKb: String(sizeKb || '1')};
})()
"@
$downloadKickoffValue = Get-RuntimeValue $downloadKickoff
if ($downloadKickoffValue) {
    Write-Output ("다운로드 호출: " + (($downloadKickoffValue | ConvertTo-Json -Compress -Depth 8)))
    if ($downloadKickoffValue.mode -eq "direct-raonk-filestore" -and $downloadKickoffValue.href -and $downloadKickoffValue.fileName) {
        $cookieHeader = Get-BrowserCookieHeader -Socket $socket
        $targetPath = Join-Path $downloadPath ([string]$downloadKickoffValue.fileName)
        Write-Output "대용량 파일 직접 저장 시작: $targetPath"
        Download-FileWithCookies -Url ([string]$downloadKickoffValue.href) -OutFile $targetPath -CookieHeader $cookieHeader
        if (Test-ZipFile -Path $targetPath) {
            Write-Output "다운로드 완료: $targetPath"
            Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
            exit 0
        }
        $file = Get-Item -LiteralPath $targetPath
        Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
        throw "대용량 파일 직접 저장 결과가 비정상입니다: $($file.FullName), $($file.Length) bytes"
    }
}

$downloadDeadline = (Get-Date).AddSeconds($DownloadWaitSeconds)
$raonkInstalled = $false
do {
    $installer = Get-ChildItem -LiteralPath $downloadPath -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ieq "raonkSetup.exe" -and $_.Length -gt 1024 * 1024 } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($installer -and -not $raonkInstalled) {
        Write-Output "라온K 다운로드 모듈 설치 파일이 내려왔습니다. 설치를 실행한 뒤 대용량 다운로드를 다시 시도합니다: $($installer.FullName)"
        $install = Start-Process -FilePath $installer.FullName -ArgumentList "/S" -Wait -PassThru
        Write-Output "라온K 설치 종료 코드: $($install.ExitCode)"
        $raonkInstalled = $true
        Start-Sleep -Seconds 5
        Invoke-Js -Socket $socket -Expression @"
(() => {
  const targetDsId = $dsIdJson;
  const targetFileNo = $fileNoJson;
  for (const checkbox of [...document.querySelectorAll('input[name=chkDs]')]) {
    checkbox.checked = String(checkbox.value) === targetFileNo;
    checkbox.dispatchEvent(new Event('change', {bubbles: true}));
  }
  if (window.listFnc && typeof listFnc.multiDownload === 'function') {
    listFnc.multiDownload(targetDsId);
    return {mode: 'retry-listFnc.multiDownload'};
  }
  return {mode: 'retry-unavailable'};
})()
"@ | Out-Null
    }

    $files = Get-ChildItem -LiteralPath $downloadPath -File -ErrorAction SilentlyContinue |
        Where-Object {
            $_.FullName -notin $before -and
            $_.Name -notlike "*.crdownload" -and
            $_.Extension -ieq ".zip" -and
            $_.LastWriteTime -ge $downloadStart -and
            (Test-ZipFile -Path $_.FullName)
        } |
        Sort-Object LastWriteTime -Descending
    if ($files) {
        $file = $files | Select-Object -First 1
        Write-Output "다운로드 완료: $($file.FullName)"
        Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
        exit 0
    }
    Write-Output "ZIP 파일 다운로드가 끝나기를 기다리는 중입니다."
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $downloadDeadline)

Close-VWorldAutomationPages -Socket $socket -Port $RemoteDebuggingPort
$debugKickoff = if ($downloadKickoffValue) { ($downloadKickoffValue | ConvertTo-Json -Compress -Depth 8) } else { "NO_KICKOFF_VALUE" }
$debugDirect = if ($directAttemptDebug.Count -gt 0) { ($directAttemptDebug -join " | ") } else { "NO_DIRECT_ATTEMPT_DETAIL" }
throw "VWorld ZIP 다운로드가 차단되었습니다. 자동 로그인/다운로드 호출 정보를 확인해주세요. direct=$debugDirect kickoff=$debugKickoff"
