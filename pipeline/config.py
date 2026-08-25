"""Загрузка конфигурации проекта из config.json."""
import json
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "config.json"

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


INPUT_DIR = PROJECT_DIR / "input"
OUTPUT_DIR = PROJECT_DIR / "output"
MUSIC_DIR = PROJECT_DIR / "music"
WORK_DIR = PROJECT_DIR / "work"
LOGS_DIR = PROJECT_DIR / "logs"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".ts", ".wmv"}
MUSIC_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"}
TEXT_EXTENSIONS = {".txt"}
