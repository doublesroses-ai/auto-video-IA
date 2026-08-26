"""Где лежит код и где — данные пользователя.

Два режима:
* «из папки проекта» (есть .git или .venv) — всё рядом с кодом, как раньше;
* «установленное приложение» — код в Program Files/LocalAppData только на чтение,
  а данные пользователя в отдельных папках, которые переживают переустановку.

Пути принципиально без кириллицы: ffmpeg получает их внутрь -filter_complex,
и не-ASCII символы там ломают фильтры субтитров на Windows.
Тяжёлые папки держим вне Документов: OneDrive умеет незаметно включить
их синхронизацию, и тогда стримы на гигабайты уедут в облако.
"""
import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
APP_NAME = "AutoVideoIA"


def is_portable() -> bool:
    """True, если работаем прямо из папки с исходниками (как сейчас у автора)."""
    return (APP_DIR / ".git").exists() or (APP_DIR / ".venv").exists()


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Local"


def _videos_dir() -> Path:
    """Папка «Видео»: в отличие от Документов, OneDrive её обычно не синхронизирует."""
    videos = Path.home() / "Videos"
    return videos if videos.is_dir() else Path.home()


def user_dir() -> Path:
    """Видимые пользователю папки: input, output, music, backgrounds, config.json."""
    override = os.environ.get("AUTOVIDEO_USER_DIR")
    if override:
        return Path(override)
    if is_portable():
        return APP_DIR
    return _videos_dir() / APP_NAME


def cache_dir() -> Path:
    """Служебные папки: work, logs, watcher.lock. Их не жалко потерять."""
    override = os.environ.get("AUTOVIDEO_CACHE_DIR")
    if override:
        return Path(override)
    if is_portable():
        return APP_DIR
    return _local_appdata() / APP_NAME


def python_exe(windowless: bool = False) -> str:
    """Интерпретатор для запуска наблюдателя отдельным процессом."""
    name = "pythonw.exe" if windowless else "python.exe"
    if is_portable():
        candidate = APP_DIR / ".venv" / "Scripts" / name
        if candidate.exists():
            return str(candidate)
    # установленное приложение: рядом с sys.executable лежит и pythonw
    exe = Path(sys.executable)
    sibling = exe.with_name(name)
    return str(sibling if sibling.exists() else exe)


def bundled_bin() -> Path:
    """Папка со вшитыми ffmpeg/ffprobe (появляется в установленной версии)."""
    return APP_DIR / "bin"
