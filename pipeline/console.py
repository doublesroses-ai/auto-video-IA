"""Вывод в консоль без падений на кириллице и стрелках.

Консоль Windows по умолчанию живёт в однобайтовой кодировке (cp866/cp1251),
в которой нет символа «→» и многих других. Любое сообщение с ним обрывало
программу с UnicodeEncodeError вместо того, чтобы просто напечататься.
"""
import sys


def use_utf8() -> None:
    """Переводит вывод в UTF-8. Вызывать в начале запускаемых скриптов."""
    for stream in (sys.stdout, sys.stderr):
        # при запуске через pythonw консоли нет и потоки равны None
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass
