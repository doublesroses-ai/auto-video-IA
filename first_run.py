"""Окно первого запуска: докачивает тяжёлые части, которых нет в установщике.

Что докачивается и зачем:
  * библиотеки CUDA (~2 ГБ) — только при видеокарте NVIDIA, ускоряют распознавание
    речи в десятки раз; без них всё работает, но на процессоре;
  * модель распознавания речи Whisper (~1,5 ГБ) — обязательна;
  * Ollama и модель отбора моментов (~5 ГБ) — по желанию, придумывает заголовки.

Каждый шаг можно пропустить и вернуться к нему позже.
"""
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from pipeline.paths import python_exe  # noqa: E402
from pipeline.config import ensure_dirs  # noqa: E402
from pipeline.paths import user_dir  # noqa: E402

MARKER = APP_DIR / "first_run_done.marker"
LIB_DIR = APP_DIR / "lib"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def has_nvidia() -> bool:
    if shutil.which("nvidia-smi"):
        return True
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=25, creationflags=NO_WINDOW)
        return "NVIDIA" in (out.stdout or "").upper()
    except Exception:
        return False


def ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA", "")
    candidate = Path(local) / "Programs" / "Ollama" / "ollama.exe"
    return str(candidate) if candidate.exists() else None


class Setup(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Автомонтаж видео — первая настройка")
        self.geometry("620x440")
        self.resizable(False, False)

        ttk.Label(self, text="Первая настройка", font=("Segoe UI", 15, "bold")
                  ).pack(anchor="w", padx=20, pady=(18, 2))
        ttk.Label(self, wraplength=575, justify="left",
                  text="Осталось скачать то, что не поместилось в установщик. "
                       "Это делается один раз, нужен интернет.\n"
                       "Любой шаг можно пропустить — программа будет работать, "
                       "просто медленнее или без части возможностей."
                  ).pack(anchor="w", padx=20, pady=(0, 12))

        frame = ttk.Frame(self)
        frame.pack(fill="x", padx=20)
        self.labels = {}
        rows = (
            ("cuda", "Ускорение на видеокарте NVIDIA (~2 ГБ)"),
            ("whisper", "Модель распознавания речи (~1,5 ГБ)"),
            ("punct", "Расстановка запятых в субтитрах (~500 МБ)"),
            ("ollama", "Нейросеть отбора моментов (~5 ГБ, по желанию)"),
        )
        for key, text in rows:
            row = ttk.Frame(frame)
            row.pack(fill="x", pady=3)
            ttk.Label(row, text="•  " + text, width=52, anchor="w").pack(side="left")
            state = ttk.Label(row, text="ожидает", foreground="gray")
            state.pack(side="left")
            self.labels[key] = state

        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.progress.pack(fill="x", padx=20, pady=(16, 4))
        self.status = ttk.Label(self, text="Готово к настройке", wraplength=575,
                                justify="left")
        self.status.pack(anchor="w", padx=20)

        btns = ttk.Frame(self)
        btns.pack(side="bottom", fill="x", padx=20, pady=14)
        self.skip_btn = ttk.Button(btns, text="Пропустить всё", command=self.finish)
        self.skip_btn.pack(side="right", padx=4)
        self.start_btn = ttk.Button(btns, text="Начать настройку", command=self.start)
        self.start_btn.pack(side="right", padx=4)
        self.ollama_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(btns, text="ставить нейросеть отбора моментов",
                        variable=self.ollama_var).pack(side="left")

    # ---------- служебное ----------

    def set_state(self, key: str, text: str, color: str = "black") -> None:
        self.after(0, lambda: self.labels[key].config(text=text, foreground=color))

    def say(self, text: str) -> None:
        self.after(0, lambda: self.status.config(text=text))

    def run(self, cmd: list[str], timeout: int = 3600) -> bool:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout,
                                  creationflags=NO_WINDOW)
            if proc.returncode != 0:
                print((proc.stderr or "")[-1500:])
            return proc.returncode == 0
        except Exception as exc:
            print(f"{cmd[0]}: {exc}")
            return False

    # ---------- шаги ----------

    def step_cuda(self) -> None:
        if not has_nvidia():
            self.set_state("cuda", "видеокарты NVIDIA нет — пропущено", "gray")
            return
        self.set_state("cuda", "качаю...", "blue")
        self.say("Качаю библиотеки CUDA. Самая долгая часть, около 2 ГБ.")
        ok = self.run([python_exe(), "-m", "pip", "install", "--no-warn-script-location",
                       "--target", str(LIB_DIR), "--upgrade",
                       "-r", str(APP_DIR / "requirements-gpu.txt")])
        self.set_state("cuda", "готово" if ok else "не вышло, буду считать на процессоре",
                       "green" if ok else "darkorange")

    def step_whisper(self) -> None:
        self.set_state("whisper", "качаю...", "blue")
        self.say("Качаю модель распознавания речи, около 1,5 ГБ.")
        code = ("from faster_whisper import WhisperModel;"
                "WhisperModel('{}', device='cpu', compute_type='int8')")
        if self.run([python_exe(), "-c", code.format("large-v3-turbo")]):
            self.set_state("whisper", "готово", "green")
        elif self.run([python_exe(), "-c", code.format("small")]):
            self.set_state("whisper", "готово (облегчённая модель)", "green")
        else:
            self.set_state("whisper", "не вышло — скачается при первой обработке",
                           "darkorange")

    def step_punctuation(self) -> None:
        """Silero расставляет точки и запятые — от них зависят границы клипов."""
        self.set_state("punct", "качаю...", "blue")
        self.say("Качаю библиотеку для расстановки знаков препинания, около 500 МБ.")
        ok = self.run([python_exe(), "-m", "pip", "install", "--no-warn-script-location",
                       "--target", str(LIB_DIR),
                       "-r", str(APP_DIR / "requirements-punctuation.txt")])
        if ok:
            self.say("Качаю саму модель пунктуации, около 90 МБ.")
            self.run([python_exe(), "-c",
                      "import torch; torch.hub.load('snakers4/silero-models',"
                      "'silero_te', trust_repo=True, verbose=False)"], timeout=900)
        self.set_state("punct", "готово" if ok else "не вышло — субтитры без запятых",
                       "green" if ok else "darkorange")

    def step_ollama(self) -> None:
        if not self.ollama_var.get():
            self.set_state("ollama", "пропущено по выбору", "gray")
            return
        self.set_state("ollama", "устанавливаю...", "blue")
        if not ollama_exe():
            self.say("Устанавливаю Ollama. Это отдельная программа: при удалении "
                     "автомонтажа она останется и её надо удалять вручную.")
            if not self.run(["winget", "install", "--id", "Ollama.Ollama", "-e",
                             "--accept-source-agreements",
                             "--accept-package-agreements"]):
                self.set_state("ollama", "не вышло — заголовки будут проще", "darkorange")
                return
        exe = ollama_exe()
        if not exe:
            self.set_state("ollama", "не вышло — заголовки будут проще", "darkorange")
            return
        self.say("Качаю модель отбора моментов, около 5 ГБ.")
        ok = self.run([exe, "pull", "qwen3:8b"])
        self.set_state("ollama", "готово" if ok else "не вышло — заголовки будут проще",
                       "green" if ok else "darkorange")

    # ---------- управление ----------

    def start(self) -> None:
        self.start_btn.config(state="disabled")
        self.skip_btn.config(text="Свернуть", command=self.iconify)
        self.progress.start(80)
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        try:
            ensure_dirs()
            self.step_cuda()
            self.step_whisper()
            self.step_punctuation()
            self.step_ollama()
            self.say(f"Настройка завершена. Папка для видео: {user_dir()}")
        finally:
            self.after(0, self._done)

    def _done(self) -> None:
        self.progress.stop()
        self.skip_btn.config(text="Закрыть", command=self.finish)
        messagebox.showinfo(
            "Готово",
            "Настройка завершена.\n\nКидай видео в папку input — результат появится "
            f"в output:\n{user_dir()}")

    def finish(self) -> None:
        try:
            MARKER.write_text("ok", encoding="ascii")
        except OSError:
            pass
        self.destroy()


def needed() -> bool:
    """True, если первая настройка ещё не проводилась."""
    return not MARKER.exists()


if __name__ == "__main__":
    Setup().mainloop()
