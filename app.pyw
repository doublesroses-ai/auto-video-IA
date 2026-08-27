"""Окно управления автомонтажом: наблюдатель, обработка, озвучка, правка субтитров."""
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox, scrolledtext


PROJECT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_DIR))

from pipeline import dpi  # noqa: E402

dpi.enable()  # строго до создания окна Tk, иначе Windows размоет текст

from pipeline.config import (  # noqa: E402
    load_config, ensure_dirs, INPUT_DIR, OUTPUT_DIR, LOGS_DIR, LOCK_FILE,
)
from pipeline.paths import python_exe  # noqa: E402
from pipeline.tts import VOICES  # noqa: E402

LOG_FILE = LOGS_DIR / "watcher.log"


class LogWriter:
    """stdout приложения пишется в общий лог, который показывает нижняя панель."""

    def write(self, s):
        if s.strip():
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(s if s.endswith("\n") else s + "\n")

    def flush(self):
        pass


def watcher_pid() -> int | None:
    from watcher import _pid_alive
    if not LOCK_FILE.exists():
        return None
    try:
        pid = int(LOCK_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_alive(pid) else None


class App(tk.Tk):
    def px(self, value: int) -> int:
        """Пересчёт размера из «обычного» экрана в пиксели текущего."""
        return int(value * self.k)

    def __init__(self):
        super().__init__()
        self.title("Автомонтаж видео")
        # Размеры в коде рассчитаны на обычный экран; на экране с увеличенным
        # масштабом их надо умножить, иначе окно выйдет мелким.
        self.k = dpi.scale_of(self)
        self.geometry(f"{self.px(760)}x{self.px(640)}")
        self.minsize(self.px(640), self.px(520))
        self.busy = False
        self._log_size = -1
        ensure_dirs()
        sys.stdout = sys.stderr = LogWriter()

        pad = {"padx": self.px(10), "pady": self.px(6)}

        # --- Наблюдатель ---
        top = ttk.LabelFrame(self, text="Автоматический режим (папка input)")
        top.pack(fill="x", **pad)
        self.watcher_label = ttk.Label(top, text="...")
        self.watcher_label.pack(side="left", padx=self.px(8), pady=self.px(8))
        ttk.Button(top, text="Открыть output", command=lambda: os.startfile(OUTPUT_DIR)
                   ).pack(side="right", padx=self.px(4), pady=self.px(6))
        ttk.Button(top, text="Открыть input", command=lambda: os.startfile(INPUT_DIR)
                   ).pack(side="right", padx=self.px(4), pady=self.px(6))
        self.watcher_btn = ttk.Button(top, text="Запустить", command=self.toggle_watcher)
        self.watcher_btn.pack(side="right", padx=self.px(4), pady=self.px(6))

        # --- Вкладки ---
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=False, **pad)

        tab1 = ttk.Frame(nb)
        nb.add(tab1, text="  Видео → шортсы  ")
        ttk.Label(tab1, text="Выбери длинное видео — получишь шортсы 9:16 и версию 16:9.\n"
                             "(То же самое происходит само с файлами из папки input.)"
                  ).pack(anchor="w", padx=self.px(10), pady=self.px(8))
        self.video_btn = ttk.Button(tab1, text="Выбрать видео и обработать…",
                                    command=self.pick_video)
        self.video_btn.pack(anchor="w", padx=self.px(10), pady=(self.px(0), self.px(10)))

        tab2 = ttk.Frame(nb)
        nb.add(tab2, text="  Текст → озвучка  ")
        row = ttk.Frame(tab2)
        row.pack(fill="x", padx=self.px(10), pady=(self.px(8), self.px(2)))
        ttk.Label(row, text="Голос:").pack(side="left")
        self.voice_var = tk.StringVar(value=list(VOICES)[0])
        ttk.Combobox(row, textvariable=self.voice_var, values=list(VOICES),
                     state="readonly", width=22).pack(side="left", padx=self.px(6))
        ttk.Button(row, text="Загрузить из .txt…", command=self.load_txt
                   ).pack(side="right")
        self.text_box = tk.Text(tab2, height=6, wrap="word", font=("Segoe UI", 10))
        self.text_box.pack(fill="both", expand=True, padx=self.px(10), pady=self.px(4))
        self.tts_btn = ttk.Button(tab2, text="Озвучить и собрать видео",
                                  command=self.run_tts)
        self.tts_btn.pack(anchor="w", padx=self.px(10), pady=(self.px(2), self.px(10)))

        tab3 = ttk.Frame(nb)
        nb.add(tab3, text="  Готовые проекты  ")
        ttk.Label(tab3, text="Поправить заголовки, которые придумала нейросеть,\n"
                             "исправить ошибки распознавания или заново выбрать моменты."
                  ).pack(anchor="w", padx=self.px(10), pady=self.px(8))
        row3 = ttk.Frame(tab3)
        row3.pack(fill="x", padx=self.px(10), pady=(self.px(0), self.px(6)))
        self.project_var = tk.StringVar()
        self.project_combo = ttk.Combobox(row3, textvariable=self.project_var,
                                          state="readonly", width=40)
        self.project_combo.pack(side="left")
        ttk.Button(row3, text="Обновить список", command=self.refresh_projects
                   ).pack(side="left", padx=self.px(6))
        row3b = ttk.Frame(tab3)
        row3b.pack(fill="x", padx=self.px(10), pady=(0, self.px(10)))
        self.title_btn = ttk.Button(row3b, text="Заголовки роликов…",
                                    command=self.open_titles)
        self.title_btn.pack(side="left")
        self.edit_btn = ttk.Button(row3b, text="Субтитры…", command=self.open_editor)
        self.edit_btn.pack(side="left", padx=self.px(6))
        self.repick_btn = ttk.Button(row3b, text="Выбрать моменты заново",
                                     command=self.repick)
        self.repick_btn.pack(side="left")

        # --- Статус и лог ---
        self.progress = ttk.Progressbar(self, mode="indeterminate")
        self.status_label = ttk.Label(self, text="Готов к работе")
        self.status_label.pack(anchor="w", padx=self.px(12))
        log_frame = ttk.LabelFrame(self, text="Журнал")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_box = scrolledtext.ScrolledText(
            log_frame, height=10, state="disabled", font=("Consolas", 9))
        self.log_box.pack(fill="both", expand=True, padx=self.px(4), pady=self.px(4))

        self.refresh_projects()
        self.after(300, self._tick)
        threading.Thread(target=self._check_update, daemon=True).start()

    # ---------- обновления ----------

    def _check_update(self):
        """Тихо спрашивает GitHub о новой версии. Нет интернета — молчим."""
        try:
            import updater
        except ImportError:
            return
        info = updater.check()
        if info:
            self.after(0, lambda: self._offer_update(updater, info))

    def _offer_update(self, updater, info):
        if not messagebox.askyesno("Есть обновление",
                                   updater.describe(info) + "\n\nУстановить сейчас?"):
            return
        self.status_label.config(text="Качаю обновление...")
        self.progress.pack(fill="x", padx=self.px(12), pady=self.px(2), before=self.status_label)
        self.progress.config(mode="determinate", maximum=100, value=0)

        def work():
            try:
                path = updater.download(
                    info, progress=lambda d, t: self.after(
                        0, lambda: self.progress.config(value=100 * d / t)))
                self.after(0, lambda: updater.install(path))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Обновление", str(exc)))
                self.after(0, lambda: self.progress.pack_forget())

        threading.Thread(target=work, daemon=True).start()

    # ---------- фоновые задачи ----------

    def _run_in_thread(self, fn, done_msg: str):
        if self.busy:
            messagebox.showwarning("Занято", "Дождись завершения текущей задачи.")
            return
        self.busy = True
        self._set_busy_ui(True)

        def worker():
            try:
                fn()
                self.after(0, lambda: self.status_label.config(text=done_msg))
                self.after(0, lambda: messagebox.showinfo("Готово", done_msg))
            except Exception as exc:
                print(f"ОШИБКА: {exc}")
                self.after(0, lambda exc=exc: messagebox.showerror("Ошибка", str(exc)))
                self.after(0, lambda: self.status_label.config(text="Ошибка — детали в журнале"))
            finally:
                self.busy = False
                self.after(0, lambda: self._set_busy_ui(False))

        threading.Thread(target=worker, daemon=True).start()

    def _set_busy_ui(self, busy: bool):
        state = "disabled" if busy else "normal"
        for btn in (self.video_btn, self.tts_btn, self.edit_btn,
                    self.title_btn, self.repick_btn):
            btn.config(state=state)
        if busy:
            self.status_label.config(text="Работаю… ход обработки — в журнале ниже")
            self.progress.pack(fill="x", padx=self.px(12), pady=self.px(2), before=self.status_label)
            self.progress.start(80)
        else:
            self.progress.stop()
            self.progress.pack_forget()
            self.refresh_projects()

    # ---------- наблюдатель ----------

    def toggle_watcher(self):
        pid = watcher_pid()
        if pid:
            if not messagebox.askyesno(
                    "Остановить наблюдатель?",
                    "Если сейчас идёт обработка файла, она прервётся.\nОстановить?"):
                return
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
            LOCK_FILE.unlink(missing_ok=True)
            print("Наблюдатель остановлен из приложения.")
        else:
            subprocess.Popen([python_exe(), str(PROJECT_DIR / "watcher.py")],
                             cwd=str(PROJECT_DIR),
                             creationflags=subprocess.CREATE_NO_WINDOW)
            print("Наблюдатель запущен из приложения.")

    # ---------- вкладка 1: видео ----------

    def pick_video(self):
        path = filedialog.askopenfilename(
            title="Выбери видео",
            filetypes=[("Видео", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts *.wmv"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        from pipeline.process import process_video
        self._run_in_thread(lambda: process_video(path, load_config()),
                            f"Готово: {Path(path).stem} → папка output")

    # ---------- вкладка 2: озвучка ----------

    def load_txt(self):
        path = filedialog.askopenfilename(
            title="Текст для озвучки", filetypes=[("Текст", "*.txt")])
        if not path:
            return
        raw = Path(path).read_bytes()
        for enc in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                text = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            messagebox.showerror("Ошибка", "Не удалось прочитать файл.")
            return
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", text)

    def run_tts(self):
        text = self.text_box.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("Пусто", "Введи или загрузи текст для озвучки.")
            return
        cfg = load_config()
        cfg["tts"]["voice"] = VOICES[self.voice_var.get()]
        name = "ozvuchka_" + time.strftime("%Y%m%d_%H%M%S")
        from pipeline.text_video import text_to_video
        self._run_in_thread(lambda: text_to_video(text, name, cfg),
                            f"Готово: {name} → папка output")

    # ---------- вкладка 3: субтитры ----------

    def refresh_projects(self):
        from pipeline.rerender import list_projects
        projects = list_projects()
        self.project_combo["values"] = projects
        if projects and not self.project_var.get():
            self.project_var.set(projects[0])

    def _current_project(self) -> str | None:
        project = self.project_var.get()
        if not project:
            messagebox.showwarning("Нет проекта", "Сначала выбери проект из списка.")
            return None
        return project

    def repick(self):
        project = self._current_project()
        if not project:
            return
        if not messagebox.askyesno(
                "Выбрать моменты заново",
                "Нейросеть заново просмотрит всё видео и соберёт новые шортсы.\n"
                "Прежние ролики этого проекта будут заменены.\n\nПродолжить?"):
            return
        from pipeline.rerender import repick_and_render
        self._run_in_thread(lambda: repick_and_render(project),
                            f"Моменты выбраны заново: {project}")

    def open_titles(self):
        """Правка заголовков перед тем, как вшить их в кадр."""
        project = self._current_project()
        if not project:
            return
        from pipeline.rerender import load_titles, apply_titles
        try:
            titles = load_titles(project)
        except Exception as exc:
            messagebox.showerror("Не вышло", str(exc))
            return
        if not titles:
            messagebox.showinfo("Пусто", "У этого проекта нет шортсов с заголовками.")
            return

        win = tk.Toplevel(self)
        win.title(f"Заголовки: {project}")
        win.geometry(f"{self.px(660)}x{self.px(120 + 60 * len(titles))}")
        ttk.Label(win, wraplength=self.px(620), justify="left",
                  text="Эти заголовки нейросеть придумала сама — она нередко ошибается "
                       "в падежах. Поправь, и ролики пересоберутся с исправленным титром."
                  ).pack(anchor="w", padx=self.px(12), pady=self.px(10))
        entries = []
        for item in titles:
            row = ttk.Frame(win)
            row.pack(fill="x", padx=self.px(12), pady=self.px(4))
            ttk.Label(row, text=item["file"].split("/")[-1], width=16
                      ).pack(side="left")
            var = tk.StringVar(value=item["hook"])
            ttk.Entry(row, textvariable=var).pack(side="left", fill="x", expand=True)
            entries.append(var)

        def apply():
            new = [v.get().strip() for v in entries]
            win.destroy()
            self._run_in_thread(lambda: apply_titles(project, new),
                                f"Заголовки обновлены, ролики пересобраны: {project}")

        ttk.Button(win, text="Применить и пересобрать ролики", command=apply
                   ).pack(anchor="e", padx=self.px(12), pady=self.px(10))

    def open_editor(self):
        project = self._current_project()
        if not project:
            return
        from pipeline.rerender import load_segments
        segments = load_segments(project)

        win = tk.Toplevel(self)
        win.title(f"Субтитры: {project}")
        win.geometry("720x480")
        ttk.Label(win, text="Одна строка — один фрагмент. Правь текст, "
                            "но не удаляй и не добавляй строки.").pack(anchor="w", padx=self.px(10), pady=self.px(6))
        editor = scrolledtext.ScrolledText(win, wrap="word", font=("Segoe UI", 11))
        editor.pack(fill="both", expand=True, padx=self.px(10), pady=self.px(4))
        editor.insert("1.0", "\n".join(s["text"] for s in segments))

        def apply():
            lines = [ln.strip() for ln in editor.get("1.0", "end").splitlines()]
            while lines and not lines[-1]:
                lines.pop()
            if len(lines) != len(segments):
                messagebox.showerror(
                    "Не совпадает число строк",
                    f"Сегментов {len(segments)}, а строк {len(lines)}. "
                    "Верни удалённые строки (пустые строки не считаются в конце).",
                    parent=win)
                return
            win.destroy()
            from pipeline.rerender import apply_corrections
            self._run_in_thread(lambda: apply_corrections(project, lines),
                                f"Субтитры исправлены, ролики перерендерены: {project}")

        ttk.Button(win, text="Применить и перерендерить", command=apply
                   ).pack(anchor="e", padx=self.px(10), pady=self.px(8))

    # ---------- журнал и статус ----------

    def _tick(self):
        pid = watcher_pid()
        if pid:
            self.watcher_label.config(text=f"Наблюдатель работает (PID {pid})", foreground="green")
            self.watcher_btn.config(text="Остановить")
        else:
            self.watcher_label.config(text="Наблюдатель выключен", foreground="red")
            self.watcher_btn.config(text="Запустить")

        try:
            size = LOG_FILE.stat().st_size
        except OSError:
            size = 0
        if size != self._log_size:
            self._log_size = size
            try:
                lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-150:]
            except OSError:
                lines = []
            self.log_box.config(state="normal")
            self.log_box.delete("1.0", "end")
            self.log_box.insert("1.0", "\n".join(lines))
            self.log_box.see("end")
            self.log_box.config(state="disabled")

        self.after(1000, self._tick)


if __name__ == "__main__":
    App().mainloop()
