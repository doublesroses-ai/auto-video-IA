"""Наблюдатель за папкой input: новые видео обрабатываются автоматически.

Запуск:  python watcher.py
Готовые результаты появляются в output/, исходники переезжают в input/done/.
"""
import subprocess
import sys
import time
import traceback
from pathlib import Path

from pipeline.config import (
    load_config, ensure_dirs, INPUT_DIR, LOGS_DIR, LOCK_FILE,
    VIDEO_EXTENSIONS, TEXT_EXTENSIONS,
)
from pipeline.process import process_video
from pipeline.text_video import text_file_to_video

POLL_SEC = 15


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
    ensure_dirs()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
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


def integrity_ok(path: Path) -> bool:
    """Начало и конец видео декодируются без ошибок.

    Загрузки (например, из Telegram) идут рывками: размер может «замереть»,
    хотя файл ещё не докачан. Битый хвост ловим быстрым пробным декодированием.
    """
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return True
    from pipeline.ffmpeg_utils import ffmpeg
    for args in (["-t", "5", "-i", str(path)], ["-sseof", "-5", "-i", str(path)]):
        try:
            proc = subprocess.run(
                [ffmpeg(), "-v", "error", *args, "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180,
            )
        except subprocess.TimeoutExpired:
            return False
        if proc.returncode != 0 or (proc.stderr or "").strip():
            return False
    return True


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

    handled = VIDEO_EXTENSIONS | TEXT_EXTENSIONS
    log(f"Наблюдаю за папкой {INPUT_DIR} (проверка каждые {POLL_SEC} с). Ctrl+C — выход.")
    log("Видеофайлы нарезаются на шортсы, .txt озвучиваются нейроголосом.")
    MAX_WAIT_POLLS = 40  # ~10 минут ожидания докачки, потом файл считается битым
    waiting: dict[str, int] = {}
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
                if not integrity_ok(item):
                    waiting[item.name] = waiting.get(item.name, 0) + 1
                    if waiting[item.name] >= MAX_WAIT_POLLS:
                        log(f"{item.name}: файл так и не стал читаться — похоже, "
                            "он повреждён или загрузка оборвалась. Переношу в failed. "
                            "Скачай/скопируй файл заново и положи в input ещё раз.")
                        item.replace(failed_dir / item.name)
                        waiting.pop(item.name, None)
                    elif waiting[item.name] % 4 == 1:
                        log(f"{item.name}: файл ещё не докачан (или повреждён), жду...")
                    continue
                waiting.pop(item.name, None)
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
