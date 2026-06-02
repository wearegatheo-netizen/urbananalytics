Option Explicit

Dim fso, shell, folder, pythonw, python, script, command
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

folder = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe"
python = "C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
script = folder & "\cadastre_bridge_server.py"

shell.Run "powershell.exe -NoProfile -WindowStyle Hidden -Command ""netstat -ano | Select-String ':8788 .*LISTENING' | ForEach-Object { ($_ -split '\s+')[-1] } | Select-Object -Unique | ForEach-Object { Stop-Process -Id ([int]$_) -Force -ErrorAction SilentlyContinue }""", 0, True

If fso.FileExists(pythonw) Then
    command = """" & pythonw & """ """ & script & """ 8788"
ElseIf fso.FileExists(python) Then
    command = """" & python & """ """ & script & """ 8788"
Else
    command = "python """ & script & """ 8788"
End If

shell.CurrentDirectory = folder
shell.Run command, 0, False
WScript.Sleep 1500
shell.Run "http://127.0.0.1:8788/", 1, False
