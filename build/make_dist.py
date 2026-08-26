"""Сборка переносимой версии приложения (без Python на машине пользователя).

Делает папку build/dist/AutoVideoIA, которую потом упаковывает Inno Setup:
  python/  — вшитый Python (embeddable) + доложенный tkinter
  lib/     — библиотеки из requirements.txt
  bin/     — ffmpeg и ffprobe
  vendor/  — установщик C++ runtime, который Inno запустит при установке
  остальное — сам код приложения

Запуск:  py build/make_dist.py
"""
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = BUILD / "dist" / "AutoVideoIA"
CACHE = BUILD / "cache"

PY_VER = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
PY_TAG = f"{sys.version_info.major}{sys.version_info.minor}"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VER}/python-{PY_VER}-embed-amd64.zip"
GETPIP_URL = "https://bootstrap.pypa.io/get-pip.py"
VCREDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
# essentials вместо full_build: 98 МБ против 212 МБ на файл, все нужные фильтры
# (libass с Arial Black, boxblur, sidechaincompress, loudnorm, nvenc) проверены
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"

APP_FILES = ["app.pyw", "watcher.py", "make_shorts.py", "first_run.py", "updater.py",
             "requirements-punctuation.txt", "config.json", "README.md", "CHANGELOG.md", "Автомонтаж видео.bat", "start_watcher.bat",
             "run_watcher_hidden.vbs", "requirements.txt", "requirements-gpu.txt"]
APP_DIRS = ["pipeline"]


def log(msg):
    print(f"[сборка] {msg}", flush=True)


def download(url: str, dst: Path) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.stat().st_size > 0:
        log(f"уже скачано: {dst.name}")
        return dst
    log(f"качаю {url}")
    with urllib.request.urlopen(url, timeout=300) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    log(f"  сохранено {dst.stat().st_size / 1024 / 1024:.0f} МБ")
    return dst


def step_python():
    """Вшитый Python + доложенный tkinter (в embeddable его нет)."""
    pydir = DIST / "python"
    zip_path = download(EMBED_URL, CACHE / f"python-{PY_VER}-embed-amd64.zip")
    pydir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(pydir)
    log("вшитый Python распакован")

    # tkinter: в embeddable-архиве его нет, берём из полной установки
    base = Path(sys.base_prefix)
    missing = []
    for name in ("_tkinter.pyd", "tcl86t.dll", "tk86t.dll", "zlib1.dll"):
        src = base / "DLLs" / name
        if src.exists():
            shutil.copy2(src, pydir / name)
        elif name != "zlib1.dll":
            missing.append(name)
    for sub in ("Lib/tkinter", "tcl"):
        src = base / sub
        if src.is_dir():
            shutil.copytree(src, pydir / sub, dirs_exist_ok=True)
        else:
            missing.append(sub)
    if missing:
        raise SystemExit(f"Не найдены части tkinter в {base}: {missing}")
    log("tkinter доложен")

    # pythonw.exe в embeddable есть, но проверим — без него окно откроется с консолью
    if not (pydir / "pythonw.exe").exists():
        shutil.copy2(base / "pythonw.exe", pydir / "pythonw.exe")

    # ._pth задаёт пути поиска модулей; без import site не работает pip-каталог
    pth = pydir / f"python{PY_TAG}._pth"
    entries = [f"python{PY_TAG}.zip", ".", "Lib",
               os.path.join("..", "lib"), "..", "import site"]
    pth.write_text(os.linesep.join(entries) + os.linesep, encoding="ascii")
    log(f"{pth.name} настроен")
    return pydir


def step_libs(pydir: Path):
    """Библиотеки ставим в отдельную папку lib/ рядом с python/."""
    libdir = DIST / "lib"
    libdir.mkdir(parents=True, exist_ok=True)
    getpip = download(GETPIP_URL, CACHE / "get-pip.py")
    subprocess.run([str(pydir / "python.exe"), str(getpip), "--no-warn-script-location"],
                   check=True)
    log("pip установлен во вшитый Python")
    subprocess.run(
        [str(pydir / "python.exe"), "-m", "pip", "install", "--no-warn-script-location",
         "--target", str(libdir), "-r", str(ROOT / "requirements.txt")],
        check=True)
    _strip_junk(libdir)
    size = sum(f.stat().st_size for f in libdir.rglob("*") if f.is_file())
    log(f"библиотеки установлены: {size / 1024 / 1024:.0f} МБ")


def _strip_junk(libdir: Path):
    """Убираем кэши байт-кода и тесты пакетов — это ~220 МБ впустую."""
    removed = 0
    for pattern in ("__pycache__", "tests", "test"):
        for d in list(libdir.rglob(pattern)):
            if d.is_dir():
                removed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
    for pyc in libdir.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)
    log(f"вычищено лишнего: {removed / 1024 / 1024:.0f} МБ")


def step_ffmpeg():
    """Только ffmpeg и ffprobe из облегчённой сборки — ffplay в коде не нужен."""
    bindir = DIST / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    zip_path = download(FFMPEG_URL, CACHE / "ffmpeg-essentials.zip")
    extracted = CACHE / "ffmpeg-extracted"
    if not extracted.exists():
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(extracted)
    for tool in ("ffmpeg", "ffprobe"):
        src = next(extracted.rglob(f"{tool}.exe"))
        shutil.copy2(src, bindir / f"{tool}.exe")
    # ffmpeg распространяется под GPLv3 — текст лицензии обязан ехать рядом
    (DIST / "licenses").mkdir(exist_ok=True)
    license_lines = [
        "Сборка ffmpeg (gyan.dev, essentials) распространяется под GPLv3.",
        "Текст лицензии: https://www.gnu.org/licenses/gpl-3.0.html",
        "Исходные тексты: https://github.com/FFmpeg/FFmpeg",
        "Сборочные скрипты: https://github.com/GyanD/codexffmpeg",
    ]
    (DIST / "licenses" / "ffmpeg-GPLv3.txt").write_text(
        os.linesep.join(license_lines) + os.linesep, encoding="utf-8")
    size = sum(f.stat().st_size for f in bindir.iterdir())
    log(f"ffmpeg (essentials) скопирован: {size / 1024 / 1024:.0f} МБ")


def step_vcredist():
    """C++ runtime: ctranslate2 и onnxruntime без него не запустятся."""
    vendor = DIST / "vendor"
    vendor.mkdir(parents=True, exist_ok=True)
    download(VCREDIST_URL, CACHE / "vc_redist.x64.exe")
    shutil.copy2(CACHE / "vc_redist.x64.exe", vendor / "vc_redist.x64.exe")
    log("установщик C++ runtime добавлен")


def step_app():
    for name in APP_FILES:
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, DIST / name)
    for name in APP_DIRS:
        shutil.copytree(ROOT / name, DIST / name, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("__pycache__"))
    log("код приложения скопирован")


def main():
    if DIST.exists():
        log("чищу предыдущую сборку")
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    pydir = step_python()
    step_libs(pydir)
    step_ffmpeg()
    step_vcredist()
    step_app()
    total = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    log(f"ГОТОВО: {DIST}  ({total / 1024 / 1024:.0f} МБ на диске)")


if __name__ == "__main__":
    main()
