' Запускает наблюдатель без окна консоли (для автозапуска)
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
appDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = appDir

py = appDir & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(py) Then py = appDir & "\python\pythonw.exe"

shell.Run """" & py & """ """ & appDir & "\watcher.py""", 0, False
