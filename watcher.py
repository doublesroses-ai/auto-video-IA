"""Наблюдатель за папкой input: новые видео обрабатываются автоматически.

Запуск:  python watcher.py
Готовые результаты появляются в output/, исходники переезжают в input/done/.
"""
import sys
import time
import traceback
from pathlib import Path

from pipeline.config import (
    load_config, INPUT_DIR, LOGS_DIR, VIDEO_EXTENSIONS, TEXT_EXTENSIONS, PROJECT_DIR,
)
from pipeline.process import process_video
from pipeline.text_video import text_file_to_video

POLL_SEC = 15
LOCK_FILE = PROJECT_DIR / "watcher.lock"


class _Tee:
    """Дублирует stdout в лог-файл, чтобы прогресс обработки был виден."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for st in self.streams:
            try:
                st.write(s)
                st.flush()
            except (OSError, ValueError):
                pass

    def flush(self):
        for st in self.streams:
            try:
                st.flush()
            except (OSError, ValueError):
                pass


def _install_tee() -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    logfile = open(LOGS_DIR / "watcher.log", "a", encoding="utf-8", buffering=1)
    streams = [logfile]
    if sys.stdout is not None:
        streams.insert(0, sys.stdout)
    sys.stdout = _Tee(*streams)
    sys.stderr = sys.stdout


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def is_stable(path: Path, wait: float = 4.0) -> bool:
    """Файл дописан до конца: размер не меняется в течение wait секунд."""
    try:
        size1 = path.stat().st_size
        time.sleep(wait)
        size2 = path.stat().st_size
    except OSError:
        return False
    return size1 == size2 and size1 > 0


def _pid_alive(pid: int) -> bool:
    import ctypes
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def acquire_lock() -> bool:
    """Не даём запустить второго наблюдателя одновременно."""
    import os
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            if _pid_alive(pid):
                return False
        except (OSError, ValueError):
            pass  # старый лок от умершего процесса
    LOCK_FILE.write_text(str(os.getpid()))
    return True


def main() -> int:
    _install_tee()
    if not acquire_lock():
        print("Наблюдатель уже запущен, выхожу.")
        return 0

    done_dir = INPUT_DIR / "done"
    failed_dir = INPUT_DIR / "failed"
    for d in (INPUT_DIR, done_dir, failed_dir):
        d.mkdir(parents=True, exist_ok=True)

    handled = VIDEO_EXTENSIONS | TEXT_EXTENSIONS
    log(f"Наблюдаю за папкой {INPUT_DIR} (проверка каждые {POLL_SEC} с). Ctrl+C — выход.")
    log("Видеофайлы нарезаются на шортсы, .txt озвучиваются нейроголосом.")
    try:
        while True:
            files = sorted(
                p for p in INPUT_DIR.iterdir()
                if p.is_file() and p.suffix.lower() in handled
            )
            for item in files:
                if not is_stable(item):
                    log(f"{item.name}: файл ещё копируется, жду...")
                    continue
                log(f"Новый файл: {item.name} — начинаю обработку")
                try:
                    if item.suffix.lower() in TEXT_EXTENSIONS:
                        out = text_file_to_video(item, load_config())
                    else:
                        out = process_video(item, load_config())
                    item.replace(done_dir / item.name)
                    log(f"{item.name}: готово → {out}")
                except Exception as exc:
                    log(f"{item.name}: ОШИБКА — {exc}")
                    with open(LOGS_DIR / "errors.log", "a", encoding="utf-8") as f:
                        f.write(f"\n=== {item.name} {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                        f.write(traceback.format_exc())
                    item.replace(failed_dir / item.name)
            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        log("Остановлен пользователем.")
    finally:
        LOCK_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
