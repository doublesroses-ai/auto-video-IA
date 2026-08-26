"""Обработка одного видео вручную.

Использование:
    python make_shorts.py "путь\\к\\видео.mp4" [--shorts N] [--no-music] [--no-youtube]
"""
import argparse
import sys

from pipeline.console import use_utf8
from pipeline.config import load_config
from pipeline.process import process_video


def main() -> int:
    use_utf8()
    parser = argparse.ArgumentParser(description="Автомонтаж: длинное видео → шортсы")
    parser.add_argument("video", help="путь к исходному видео")
    parser.add_argument("--shorts", type=int, help="сколько шортсов сделать")
    parser.add_argument("--no-music", action="store_true", help="без фоновой музыки")
    parser.add_argument("--no-youtube", action="store_true", help="не делать версию 16:9")
    args = parser.parse_args()

    cfg = load_config()
    if args.shorts:
        cfg["shorts"]["count"] = args.shorts
    if args.no_music:
        cfg["music"]["enabled"] = False
    if args.no_youtube:
        cfg["render_youtube_version"] = False

    try:
        process_video(args.video, cfg)
    except Exception as exc:
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
