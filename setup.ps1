# Установка «Автомонтажа видео» на новом компьютере (Windows 10/11).
# Запуск: двойной клик по setup.bat
$ErrorActionPreference = "Stop"
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj
Write-Host "=== Установка Автомонтажа видео ===" -ForegroundColor Cyan

# 1. ffmpeg
$ffmpegOk = $false
try { Get-Command ffmpeg -ErrorAction Stop | Out-Null; $ffmpegOk = $true } catch {}
if (-not $ffmpegOk) {
    $links = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
    if (Test-Path $links) {
        $ffmpegOk = (Get-ChildItem $links -Recurse -Filter "ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1) -ne $null
    }
}
if (-not $ffmpegOk) {
    Write-Host "Ставлю ffmpeg (winget)..." -ForegroundColor Yellow
    winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
} else { Write-Host "ffmpeg уже есть" -ForegroundColor Green }

# 2. Python
$python = $null
foreach ($cmd in @("python", "py")) {
    try {
        $v = & $cmd --version 2>$null
        if ($v -match "Python 3\.(1[0-9])") { $python = $cmd; break }
    } catch {}
}
if (-not $python) {
    Write-Host "Ставлю Python 3.12 (winget)..." -ForegroundColor Yellow
    winget install --id Python.Python.3.12 -e --accept-source-agreements --accept-package-agreements
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
    $python = "python"
} else { Write-Host "Python уже есть: $(& $python --version)" -ForegroundColor Green }

# 3. Виртуальное окружение и библиотеки
if (-not (Test-Path ".venv")) {
    Write-Host "Создаю окружение Python..." -ForegroundColor Yellow
    & $python -m venv .venv
}
Write-Host "Ставлю библиотеки (faster-whisper, edge-tts)..." -ForegroundColor Yellow
& ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
& ".venv\Scripts\pip.exe" install -r requirements.txt -q

# 4. Библиотеки CUDA — только если есть видеокарта NVIDIA
$gpu = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" }
if ($gpu) {
    Write-Host "Найдена $($gpu.Name) — ставлю библиотеки CUDA (это долго, ~700 МБ)..." -ForegroundColor Yellow
    & ".venv\Scripts\pip.exe" install -r requirements-gpu.txt -q
} else {
    Write-Host "Видеокарты NVIDIA нет — будет работать на процессоре (медленнее, но работает)" -ForegroundColor Yellow
}

# 4б. Ollama — умный отбор моментов локальной нейросетью (по желанию)
$ollamaInstalled = $false
try { Get-Command ollama -ErrorAction Stop | Out-Null; $ollamaInstalled = $true } catch {}
if (-not $ollamaInstalled) {
    $ans = Read-Host "Установить Ollama для умного отбора моментов нейросетью? Скачается ~6 ГБ (y/n)"
    if ($ans -eq "y") {
        winget install --id Ollama.Ollama -e --accept-source-agreements --accept-package-agreements
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        ollama pull qwen3:8b
    } else {
        Write-Host "Ок, без Ollama — отбор моментов будет по встроенным правилам" -ForegroundColor Yellow
    }
} else {
    Write-Host "Ollama уже есть — проверяю модель..." -ForegroundColor Green
    ollama pull qwen3:8b
}

# 5. Папки
foreach ($d in @("input", "output", "music", "backgrounds", "work", "logs")) {
    New-Item -ItemType Directory -Force $d | Out-Null
}

# 6. Ярлык на рабочем столе
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Автомонтаж видео.lnk"))
$lnk.TargetPath = Join-Path $proj ".venv\Scripts\pythonw.exe"
$lnk.Arguments = '"' + (Join-Path $proj "app.pyw") + '"'
$lnk.WorkingDirectory = $proj
$lnk.IconLocation = "%SystemRoot%\System32\imageres.dll,262"
$lnk.Save()

# 7. Автозапуск наблюдателя при входе в Windows
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $proj "setup_autostart.ps1")
Start-ScheduledTask -TaskName "AutoVideoIA Watcher" -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=== Готово! ===" -ForegroundColor Green
Write-Host "Ярлык «Автомонтаж видео» — на рабочем столе."
Write-Host "Кидай видео или .txt в папку input — результат появится в output."
Write-Host "Первая обработка скачает модель распознавания речи (это разово)."
