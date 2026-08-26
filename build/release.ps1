# Полная сборка релиза: переносимая папка -> установщик .exe
# Запуск:  powershell -ExecutionPolicy Bypass -File build\release.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "=== 1/3. Собираю переносимую папку ===" -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -X utf8 "build\make_dist.py"
if ($LASTEXITCODE -ne 0) { throw "Сборка папки не удалась" }

Write-Host "=== 2/3. Ищу компилятор Inno Setup ===" -ForegroundColor Cyan
$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup не найден. Установи: winget install JRSoftware.InnoSetup"
}
Write-Host "  $iscc" -ForegroundColor Green

Write-Host "=== 3/3. Компилирую установщик (несколько минут) ===" -ForegroundColor Cyan
New-Item -ItemType Directory -Force "installer\output" | Out-Null
$log = Join-Path $env:TEMP "iscc-release.log"
& $iscc (Join-Path $root "installer\app.iss") > $log 2>&1
if ($LASTEXITCODE -ne 0) {
    Get-Content $log -Tail 20
    throw "Компиляция не удалась, полный журнал: $log"
}

$exe = Get-ChildItem "installer\output\*.exe" | Sort-Object LastWriteTime | Select-Object -Last 1
Write-Host ""
Write-Host "=== ГОТОВО ===" -ForegroundColor Green
Write-Host "$($exe.FullName)"
Write-Host "Размер: $([math]::Round($exe.Length / 1MB)) МБ"
Write-Host ""
Write-Host "Выложить: создать релиз на GitHub и приложить этот файл." -ForegroundColor Yellow
Write-Host "Тег релиза должен быть больше версии в updater.py, иначе обновление не предложится."
