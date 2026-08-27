"""Загрузка конфигурации проекта из config.json."""
import json
import shutil

from .paths import APP_DIR, user_dir, cache_dir

PROJECT_DIR = APP_DIR  # оставлено для совместимости со старым кодом
CONFIG_PATH = user_dir() / "config.json"

DEFAULTS = {
    "language": "auto",
    "whisper_model": "auto",
    "silence": {
        "enabled": True,
        "noise_db": -32,
        "min_silence_sec": 0.45,
        "pad_sec": 0.12,
    },
    "shorts": {
        "count": 3,
        "min_sec": 25,
        "max_sec": 60,
        "min_gap_sec": 30,
    },
    "subtitles": {
        "uppercase": True,
        "max_words_per_card": 3,
        "font": "Arial Black",
        "vertical_font_size": 84,
        "horizontal_font_size": 56,
        "burn_in": True,       # вшивать субтитры (для игр с текстом в кадре — false)
        "show_title": True,    # показывать заголовок первые секунды ролика
    },
    "music": {
        "enabled": True,
        "volume": 0.16,
    },
    "vertical": {
        "width": 1080,
        "height": 1920,
        "background": "blur",
    },
    "render_youtube_version": True,
    "keep_work_files": True,
    "tts": {
        "voice": "ru-RU-SvetlanaNeural",
        "rate": "+0%",
    },
    "punctuation": {
        "enabled": True,
    },
    "highlights": {
        "ollama_model": "qwen3:8b",
    },
    # Имена и термины, которые Whisper иначе слышит как попало.
    # Подсказка отдаётся распознаванию ДО работы, а не правится потом.
    "vocabulary": [],
    "av_signals": {
        "enabled": True,
        "freeze_penalty": 2.0,     # штраф за застывший экран
        "dark_penalty": 1.0,       # штраф за тёмный кадр
        "energy_bonus": 0.10,      # надбавка за эмоциональную речь
    },
    "render": {
        # потолок битрейта, Мбит/с: держит файлы компактными без видимой потери
        "shorts_max_mbps": 8,
        "youtube_max_mbps": 12,
        "master_max_mbps": 14,
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            out[key] = _merge(base[key], value)
        else:
            out[key] = value
    return out


def load_config() -> dict:
    cfg = DEFAULTS
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = _merge(DEFAULTS, json.load(f))
    return cfg


INPUT_DIR = user_dir() / "input"
OUTPUT_DIR = user_dir() / "output"
MUSIC_DIR = user_dir() / "music"
BACKGROUNDS_DIR = user_dir() / "backgrounds"
WORK_DIR = cache_dir() / "work"
LOGS_DIR = cache_dir() / "logs"
LOCK_FILE = cache_dir() / "watcher.lock"

def ensure_dirs() -> None:
    """Создаёт все рабочие папки и файл настроек. Безопасно вызывать многократно."""
    for d in (INPUT_DIR, INPUT_DIR / "done", INPUT_DIR / "failed",
              OUTPUT_DIR, MUSIC_DIR, BACKGROUNDS_DIR, WORK_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    _seed_config()


def _seed_config() -> None:
    """Кладёт образец настроек туда, где программа их ищет.

    Установщик копирует config.json рядом с программой, а читается он из папки
    пользователя — без этого переноса настройки менять было бы негде.
    """
    if CONFIG_PATH.exists():
        return
    sample = APP_DIR / "config.json"
    try:
        if sample.exists() and sample.resolve() != CONFIG_PATH.resolve():
            shutil.copy2(sample, CONFIG_PATH)
        elif not sample.exists():
            CONFIG_PATH.write_text(
                json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # без файла программа работает на значениях по умолчанию


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".wmv"}
MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
TEXT_EXTENSIONS = {".txt"}
