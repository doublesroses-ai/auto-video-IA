' Запускает наблюдатель без окна консоли (для автозапуска)
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & shell.CurrentDirectory & "\.venv\Scripts\python.exe"" watcher.py", 0, False
