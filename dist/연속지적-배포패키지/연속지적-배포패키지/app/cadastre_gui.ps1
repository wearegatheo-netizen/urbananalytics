Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

function U([int[]]$Codes) {
    return -join ($Codes | ForEach-Object { [char]$_ })
}

$folderName = U @(0xC5F0, 0xC18D, 0xC9C0, 0xC801)
$labelAddress = U @(0xC8FC,0xC18C,0x0020,0x0028,0xC2DC,0xAD70,0xAD6C,0x0020,0xD3EC,0xD568,0x0029)
$labelKey = U @(0xBE0C,0xC774,0xC6D4,0xB4DC,0x0020,0x0041,0x0050,0x0049,0x0020,0xD0A4)
$labelId = U @(0xBE0C,0xC774,0xC6D4,0xB4DC,0x0020,0xC544,0xC774,0xB514)
$labelPw = U @(0xBE0C,0xC774,0xC6D4,0xB4DC,0x0020,0xBE44,0xBC00,0xBC88,0xD638)
$labelRadius = U @(0xBC18,0xACBD,0x0020,0x0028,0x006D,0x0029)
$labelOutDir = U @(0xACB0,0xACFC,0x0020,0xC800,0xC7A5,0x0020,0xD3F4,0xB354)
$labelOpenQgis = U @(0xCC98,0xB9AC,0x0020,0xD6C4,0x0020,0x0051,0x0047,0x0049,0x0053,0x0020,0xC5F4,0xAE30)
$labelRun = U @(0xC2E4,0xD589)
$labelClose = U @(0xB2EB,0xAE30)
$labelBrowse = U @(0xCC3E,0xAE30,0x002E,0x002E,0x002E)
$msgOut = U @(0xC800,0xC7A5,0x0020,0xD3F4,0xB354,0x003A,0x0020)
$msgRunning = U @(0xC2E4,0xD589,0x0020,0xC911,0xC785,0xB2C8,0xB2E4,0x002E,0x0020,0xBE0C,0xC774,0xC6D4,0xB4DC,0x0020,0xB85C,0xADF8,0xC778,0x002F,0xB2E4,0xC6B4,0xB85C,0xB4DC,0xB97C,0x0020,0xC704,0xD574,0x0020,0xBE0C,0xB77C,0xC6B0,0xC800,0xAC00,0x0020,0xC5F4,0xB9B4,0x0020,0xC218,0x0020,0xC788,0xC2B5,0xB2C8,0xB2E4,0x002E)
$msgDone = U @(0xC644,0xB8CC,0xB410,0xC2B5,0xB2C8,0xB2E4,0x002E,0x0020,0xACB0,0xACFC,0x0020,0xC800,0xC7A5,0x0020,0xD3F4,0xB354,0x003A,0x0020)
$msgPrecheck = U @(0xC785,0xB825,0xAC12,0x0020,0xC0AC,0xC804,0x0020,0xD655,0xC778,0x0020,0xC911,0x002E,0x002E,0x002E)
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
$defaultOutDir = Join-Path $desktop $folderName
New-Item -ItemType Directory -Force -Path $defaultOutDir | Out-Null
$script:outDir = $defaultOutDir

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
    return $null
}

function Get-DefaultStyle {
    $style = Join-Path (Split-Path -Parent $PSScriptRoot) "styles\cadastre-default.qml"
    if (Test-Path -LiteralPath $style) {
        return $style
    }
    return $null
}

$form = New-Object System.Windows.Forms.Form
$form.Text = $folderName
$form.Size = New-Object System.Drawing.Size(700, 520)
$form.StartPosition = "CenterScreen"
$form.Font = New-Object System.Drawing.Font("Malgun Gothic", 10)

function Add-Label($Text, $X, $Y) {
    $label = New-Object System.Windows.Forms.Label
    $label.Text = $Text
    $label.Location = New-Object System.Drawing.Point($X, $Y)
    $label.Size = New-Object System.Drawing.Size(150, 24)
    $form.Controls.Add($label)
}

function Add-TextBox($X, $Y, $Width, $Password = $false) {
    $box = New-Object System.Windows.Forms.TextBox
    $box.Location = New-Object System.Drawing.Point($X, $Y)
    $box.Size = New-Object System.Drawing.Size($Width, 26)
    if ($Password) {
        $box.UseSystemPasswordChar = $true
    }
    $form.Controls.Add($box)
    return $box
}

Add-Label $labelAddress 20 25
$addressBox = Add-TextBox 170 22 400

Add-Label $labelId 20 65
$idBox = Add-TextBox 170 62 400

Add-Label $labelPw 20 105
$pwBox = Add-TextBox 170 102 400 $true

Add-Label $labelKey 20 145
$keyBox = Add-TextBox 170 142 400

Add-Label $labelRadius 20 185
$radiusBox = Add-TextBox 170 182 120
$radiusBox.Text = "1000"

Add-Label $labelOutDir 20 225
$outDirBox = Add-TextBox 170 222 390
$outDirBox.Text = $script:outDir
$outDirBox.ReadOnly = $true

$browseButton = New-Object System.Windows.Forms.Button
$browseButton.Text = $labelBrowse
$browseButton.Location = New-Object System.Drawing.Point(570, 221)
$browseButton.Size = New-Object System.Drawing.Size(85, 30)
$browseButton.Add_Click({
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = "결과를 저장할 폴더를 선택해주세요."
    $dialog.SelectedPath = $script:outDir
    $dialog.ShowNewFolderButton = $true
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK -and $dialog.SelectedPath) {
        $script:outDir = $dialog.SelectedPath
        $outDirBox.Text = $script:outDir
        $statusBox.Text = $msgOut + $script:outDir
    }
})
$form.Controls.Add($browseButton)

$openQgisCheck = New-Object System.Windows.Forms.CheckBox
$openQgisCheck.Text = $labelOpenQgis
$openQgisCheck.Location = New-Object System.Drawing.Point(170, 262)
$openQgisCheck.Size = New-Object System.Drawing.Size(260, 26)
$openQgisCheck.Checked = $true
$form.Controls.Add($openQgisCheck)

$statusBox = New-Object System.Windows.Forms.TextBox
$statusBox.Location = New-Object System.Drawing.Point(20, 305)
$statusBox.Size = New-Object System.Drawing.Size(635, 95)
$statusBox.Multiline = $true
$statusBox.ReadOnly = $true
$statusBox.ScrollBars = "Vertical"
$statusBox.Text = $msgOut + $script:outDir
$form.Controls.Add($statusBox)

$script:runningProcess = $null
$script:runningLog = $null
$script:lastLogLine = ""
$script:lastLogLineSince = $null
$script:hintShownForLine = ""

function Get-WaitHint($Line) {
    if ($Line -like "*VWorld 로그인을 기다리는 중입니다*") {
        return "도움말: 자동 로그인이 오래 걸리면 열린 VWorld 브라우저에서 직접 로그인해 주세요. 직접 로그인하면 더 빠르게 결과물이 나옵니다."
    }
    if ($Line -like "*ZIP 파일 다운로드가 끝나기를 기다리는 중입니다*") {
        return "도움말: 브라우저 하단/상단 다운로드 알림이나 VWorld 팝업을 확인해 주세요. 다운로드가 차단되어 있으면 허용을 눌러야 합니다."
    }
    if ($Line -like "*주소를 좌표로 변환*") {
        return "도움말: 주소 확인이 오래 걸리면 시/군/구까지 포함한 전체 주소인지, VWorld API 키가 정확한지 확인해 주세요."
    }
    if ($Line -like "*QGIS*필지*선택*") {
        return "도움말: 필지 수가 많은 지역은 QGIS 처리에 시간이 걸릴 수 있습니다. 5km 반경이 너무 크면 반경을 줄이면 빨라집니다."
    }
    return $null
}

function Update-WaitHint($Text) {
    $lines = $Text -split "`r?`n" | Where-Object { $_.Trim() }
    if (-not $lines) {
        return $Text
    }
    $last = ($lines | Select-Object -Last 1).Trim()
    if ($last -ne $script:lastLogLine) {
        $script:lastLogLine = $last
        $script:lastLogLineSince = Get-Date
        return $Text
    }
    if (-not $script:lastLogLineSince) {
        $script:lastLogLineSince = Get-Date
        return $Text
    }
    $elapsed = ((Get-Date) - $script:lastLogLineSince).TotalSeconds
    if ($elapsed -ge 60 -and $script:hintShownForLine -ne $last) {
        $hint = Get-WaitHint $last
        if ($hint) {
            $script:hintShownForLine = $last
            Add-Content -LiteralPath $script:runningLog -Value $hint -Encoding UTF8
            return $Text + [Environment]::NewLine + $hint
        }
    }
    return $Text
}

function Open-ResultFolder($Path) {
    if ($Path -and (Test-Path -LiteralPath $Path)) {
        Start-Process -FilePath "explorer.exe" -ArgumentList $Path | Out-Null
    }
}

function Get-FriendlyErrorMessage($LogPath) {
    $message = "작업이 실패했습니다."
    $tip = "입력값과 열린 브라우저의 VWorld 로그인/다운로드 상태를 확인해주세요."
    $tail = ""
    if ($LogPath -and (Test-Path -LiteralPath $LogPath)) {
        $lines = Get-Content -LiteralPath $LogPath -Encoding UTF8 -ErrorAction SilentlyContinue |
            Where-Object { $_ -and $_.Trim() } |
            Select-Object -Last 12
        $tail = ($lines -join [Environment]::NewLine)
        $joined = $lines -join " "
        if ($joined -match "VWorld API|지오코딩|NOT_FOUND|getcoord|Address geocoding") {
            $tip = "주소와 VWorld API 키를 확인해주세요. 주소는 시/군/구까지 포함해 입력하는 편이 안정적입니다."
        } elseif ($joined -match "로그인|login") {
            $tip = "열린 VWorld 브라우저에서 직접 로그인한 뒤 다시 실행해주세요."
        } elseif ($joined -match "다운로드|download|ZIP") {
            $tip = "브라우저의 다운로드 차단/팝업 알림을 허용한 뒤 다시 실행해주세요."
        } elseif ($joined -match "QGIS|python-qgis") {
            $tip = "QGIS 3.x 설치 여부를 확인해주세요."
        }
    }
    $result = $message + [Environment]::NewLine + $tip
    if ($LogPath) {
        $result += [Environment]::NewLine + [Environment]::NewLine + "로그 파일: " + $LogPath
    }
    if ($tail) {
        $result += [Environment]::NewLine + [Environment]::NewLine + "마지막 로그:" + [Environment]::NewLine + $tail
    }
    return $result
}

function Test-InputValues {
    $errors = New-Object System.Collections.Generic.List[string]
    $address = $addressBox.Text.Trim()
    $key = $keyBox.Text.Trim()
    $radiusValue = 0.0

    if (-not $address) {
        $errors.Add("주소를 입력해주세요.")
    } elseif ($address.Length -lt 8 -or $address -notmatch "(시|군|구|도)") {
        $errors.Add("주소는 시/군/구를 포함한 전체 주소로 입력해주세요. 예: 서울시 관악구 승방길 77")
    }

    if (-not $key) {
        $errors.Add("브이월드 API 키를 입력해주세요.")
    } elseif ($key -notmatch "^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$") {
        $errors.Add("브이월드 API 키 형식을 확인해주세요. 예: XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX")
    }

    if (-not [double]::TryParse($radiusBox.Text.Trim(), [ref]$radiusValue)) {
        $errors.Add("반경은 숫자로 입력해주세요.")
    } elseif ($radiusValue -le 0 -or $radiusValue -gt 50000) {
        $errors.Add("반경은 1~50000m 범위로 입력해주세요.")
    }

    if (($idBox.Text.Trim() -and -not $pwBox.Text) -or (-not $idBox.Text.Trim() -and $pwBox.Text)) {
        $errors.Add("브이월드 아이디와 비밀번호는 둘 다 입력하거나 둘 다 비워주세요.")
    }

    if (-not $script:outDir) {
        $errors.Add("결과 저장 폴더를 선택해주세요.")
    } else {
        try {
            New-Item -ItemType Directory -Force -Path $script:outDir | Out-Null
            $probe = Join-Path $script:outDir ".write_test"
            Set-Content -LiteralPath $probe -Value "ok" -Encoding UTF8
            Remove-Item -LiteralPath $probe -Force
        } catch {
            $errors.Add("결과 저장 폴더에 파일을 쓸 수 없습니다. 다른 폴더를 선택해주세요.")
        }
    }

    if (-not (Find-QgisPython)) {
        $errors.Add("QGIS 실행 환경을 찾지 못했습니다. QGIS 3.x를 설치한 뒤 다시 실행해주세요.")
    }

    if ($errors.Count -gt 0) {
        return $errors -join [Environment]::NewLine
    }
    return $null
}

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({
    if (-not $script:runningProcess) {
        return
    }
    if ($script:runningLog -and (Test-Path -LiteralPath $script:runningLog)) {
        $logText = Get-Content -LiteralPath $script:runningLog -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
        $statusBox.Text = Update-WaitHint $logText
        $statusBox.SelectionStart = $statusBox.Text.Length
        $statusBox.ScrollToCaret()
    }
    if ($script:runningProcess.HasExited) {
        $timer.Stop()
        $exitCode = $script:runningProcess.ExitCode
        $script:runningProcess.Dispose()
        $script:runningProcess = $null
        $script:lastLogLine = ""
        $script:lastLogLineSince = $null
        $script:hintShownForLine = ""
        $runButton.Enabled = $true
        if ($exitCode -eq 0) {
            $statusBox.Text = $msgDone + $script:outDir
            Open-ResultFolder $script:outDir
            [System.Windows.Forms.MessageBox]::Show($msgDone + $script:outDir)
        } else {
            $friendlyError = Get-FriendlyErrorMessage $script:runningLog
            $statusBox.Text = $friendlyError
            [System.Windows.Forms.MessageBox]::Show($friendlyError, "Error")
        }
    }
})

$runButton = New-Object System.Windows.Forms.Button
$runButton.Text = $labelRun
$runButton.Location = New-Object System.Drawing.Point(475, 420)
$runButton.Size = New-Object System.Drawing.Size(85, 34)
$form.Controls.Add($runButton)

$closeButton = New-Object System.Windows.Forms.Button
$closeButton.Text = $labelClose
$closeButton.Location = New-Object System.Drawing.Point(570, 420)
$closeButton.Size = New-Object System.Drawing.Size(85, 34)
$closeButton.Add_Click({
    if ($script:runningProcess -and -not $script:runningProcess.HasExited) {
        [System.Windows.Forms.MessageBox]::Show("A job is still running. Close the Chrome/QGIS windows or wait for completion.")
        return
    }
    $form.Close()
})
$form.Controls.Add($closeButton)

$runButton.Add_Click({
    if (-not $addressBox.Text.Trim()) {
        [System.Windows.Forms.MessageBox]::Show("주소를 입력해주세요.")
        return
    }
    if (-not $keyBox.Text.Trim()) {
        [System.Windows.Forms.MessageBox]::Show("브이월드 API 키를 입력해주세요.")
        return
    }

    $statusBox.Text = $msgPrecheck
    $form.Refresh()
    $precheckError = Test-InputValues
    if ($precheckError) {
        $statusBox.Text = $precheckError
        [System.Windows.Forms.MessageBox]::Show($precheckError, "입력값 확인")
        return
    }

    $runButton.Enabled = $false
    $statusBox.Text = $msgRunning
    $form.Refresh()

    try {
        $logsDir = Join-Path $script:outDir "logs"
        New-Item -ItemType Directory -Force -Path $logsDir | Out-Null
        $script:runningLog = Join-Path $logsDir ("run_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
        $argsList = @(
            "-ExecutionPolicy", "Bypass",
            "-File", (Join-Path $PSScriptRoot "run_cadastre_browser_full.ps1"),
            "-Address", $addressBox.Text.Trim(),
            "-VWorldKey", $keyBox.Text.Trim(),
            "-Radius", $radiusBox.Text.Trim(),
            "-OutDir", $script:outDir
        )
        if ($idBox.Text.Trim() -and $pwBox.Text) {
            $argsList += @("-VWorldId", $idBox.Text.Trim(), "-VWorldPassword", $pwBox.Text)
        }
        $style = Get-DefaultStyle
        if ($style) {
            $argsList += @("-Style", $style)
        }
        if ($openQgisCheck.Checked) {
            $argsList += "-OpenQgis"
        }

        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = "powershell.exe"
        $psi.Arguments = ($argsList | ForEach-Object {
            if ($_ -match '[\s"]') {
                '"' + ($_ -replace '"', '\"') + '"'
            } else {
                $_
            }
        }) -join " "
        $psi.WorkingDirectory = $PSScriptRoot
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.StandardOutputEncoding = [System.Text.Encoding]::UTF8
        $psi.StandardErrorEncoding = [System.Text.Encoding]::UTF8
        $psi.CreateNoWindow = $true

        $script:runningProcess = New-Object System.Diagnostics.Process
        $script:runningProcess.StartInfo = $psi
        $script:runningProcess.Start() | Out-Null
        $script:runningProcess.BeginOutputReadLine()
        $script:runningProcess.BeginErrorReadLine()
        Register-ObjectEvent -InputObject $script:runningProcess -EventName OutputDataReceived -Action {
            if ($EventArgs.Data) {
                Add-Content -LiteralPath $Event.MessageData -Value $EventArgs.Data -Encoding UTF8
            }
        } -MessageData $script:runningLog | Out-Null
        Register-ObjectEvent -InputObject $script:runningProcess -EventName ErrorDataReceived -Action {
            if ($EventArgs.Data) {
                Add-Content -LiteralPath $Event.MessageData -Value $EventArgs.Data -Encoding UTF8
            }
        } -MessageData $script:runningLog | Out-Null
        $timer.Start()
    } catch {
        $friendlyError = "실행을 시작하지 못했습니다." + [Environment]::NewLine + $_.Exception.Message
        if ($script:runningLog) {
            $friendlyError += [Environment]::NewLine + [Environment]::NewLine + "로그 파일: " + $script:runningLog
        }
        $statusBox.Text = $friendlyError
        [System.Windows.Forms.MessageBox]::Show($friendlyError, "Error")
        $runButton.Enabled = $true
    }
})

[void]$form.ShowDialog()
