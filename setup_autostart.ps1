# Регистрирует автозапуск наблюдателя при входе в Windows.
# Удалить автозапуск: schtasks /Delete /TN "AutoVideoIA Watcher" /F
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$vbs = Join-Path $projectDir "run_watcher_hidden.vbs"

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`"" -WorkingDirectory $projectDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "AutoVideoIA Watcher" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Автозапуск настроен: наблюдатель будет стартовать при входе в Windows."
Write-Host "Запустить прямо сейчас: Start-ScheduledTask -TaskName 'AutoVideoIA Watcher'"
