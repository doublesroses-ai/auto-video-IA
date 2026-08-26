"""Умный отбор моментов через локальную нейросеть (Ollama).

Нейросеть читает расшифровку и сама решает, какие фрагменты самые цепляющие
и где по смыслу закончить клип. Если Ollama не запущена или ответ кривой —
тихо откатываемся на эвристику из highlights.py.
"""
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from .highlights import _split_sentences, pick_highlights

OLLAMA_URL = "http://localhost:11434"


def _server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3):
            return True
    except Exception:
        return False


def _start_server() -> bool:
    """Поднимает сервер Ollama, если он установлен, но не запущен."""
    exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
    if not exe.is_file():
        return False
    try:
        subprocess.Popen([str(exe)], creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return False
    for _ in range(12):  # ждём до ~12 секунд
        time.sleep(1)
        if _server_up():
            return True
    return False


def _ollama_chat(model: str, prompt: str, timeout: float = 420.0) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        # think=false: у «думающих» моделей (qwen3) отключаем длинные размышления —
        # иначе ответ занимает минуты. keep_alive: модель остаётся в памяти видеокарты,
        # чтобы следующее видео не ждало повторной загрузки.
        "think": False,
        "keep_alive": "15m",
        "options": {"num_ctx": 16384, "temperature": 0.3},
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["message"]["content"]


def ollama_available(model: str, autostart: bool = True) -> bool:
    if not _server_up():
        if not autostart or not _start_server():
            return False
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        return any(n == model or n.startswith(model + ":") or n.split(":")[0] == model.split(":")[0]
                   for n in names)
    except Exception:
        return False


def _fit_to_limits(sentences: list[dict], i: int, j: int,
                   min_sec: float, max_sec: float) -> int | None:
    """Подгоняет конец клипа под лимиты длительности, оставаясь на границе фразы.

    Нейросеть иногда выходит за рамки: укорачиваем с конца или удлиняем следующими
    фразами. Возвращает новый индекс последней фразы либо None, если подогнать нельзя.
    """
    start = sentences[i]["start"]

    # слишком длинный — отрезаем фразы с конца, пока не влезет
    while j > i and sentences[j]["end"] - start > max_sec:
        j -= 1
    # слишком короткий — добираем следующими фразами, не вылезая за максимум
    while j + 1 < len(sentences) and sentences[j]["end"] - start < min_sec:
        if sentences[j + 1]["end"] - start > max_sec:
            break
        j += 1

    dur = sentences[j]["end"] - start
    if dur > max_sec or dur < min_sec * 0.6:
        return None
    return j


def _build_prompt(sentences: list[dict], count: int, min_sec: float, max_sec: float) -> str:
    lines = [
        f"[{i}] ({s['start']:.0f}-{s['end']:.0f} с) {s['text']}"
        for i, s in enumerate(sentences)
    ]
    listing = "\n".join(lines)
    return f"""Ты монтажёр коротких вертикальных видео (TikTok / YouTube Shorts).
Ниже — пронумерованные фразы из расшифровки видео с таймкодами в секундах.

Выбери {count} САМЫХ цепляющих фрагментов для шортсов. Правила:
- Фрагмент — это диапазон подряд идущих фраз: от start_id до end_id включительно.
- Длительность фрагмента: от {min_sec:.0f} до {max_sec:.0f} секунд (по таймкодам).
- Фрагмент должен начинаться с интригующей фразы (хук) и заканчиваться ЗАВЕРШЁННОЙ мыслью.
- Фрагменты не должны пересекаться.
- В hook напиши цепляющий заголовок для этого шортса (до 8 слов, по-русски).

Ответь строго JSON-объектом вида:
{{"clips": [{{"start_id": 0, "end_id": 3, "hook": "заголовок"}}]}}

Фразы:
{listing}
"""


def pick_highlights_smart(transcript: dict, total_duration: float, count: int,
                          min_sec: float, max_sec: float, min_gap_sec: float,
                          model: str) -> tuple[list[dict], str]:
    """Возвращает (клипы, движок): движок 'ollama' или 'heuristic'."""
    fallback = lambda: (pick_highlights(  # noqa: E731
        transcript, total_duration, count, min_sec, max_sec, min_gap_sec), "heuristic")

    sentences = _split_sentences(transcript.get("segments", []))
    if len(sentences) < 3 or not ollama_available(model):
        return fallback()

    try:
        raw = _ollama_chat(model, _build_prompt(sentences, count, min_sec, max_sec))
        data = json.loads(raw)
        clips = []
        for item in data.get("clips", []):
            i, j = int(item["start_id"]), int(item["end_id"])
            if not (0 <= i <= j < len(sentences)):
                continue
            j = _fit_to_limits(sentences, i, j, min_sec, max_sec)
            if j is None:
                continue
            start = max(sentences[i]["start"] - 0.2, 0.0)
            end = min(sentences[j]["end"] + 0.35, total_duration)
            dur = end - start
            if dur < min_sec * 0.6:
                continue
            # пересечения отбрасываем
            if any(not (end < c["start"] or start > c["end"]) for c in clips):
                continue
            clips.append({
                "start": round(start, 2), "end": round(end, 2),
                "hook": str(item.get("hook", ""))[:120] or sentences[i]["text"][:120],
                "score": 100.0 - len(clips),  # порядок нейросети = приоритет
            })
        if clips:
            clips = clips[:count]
            # нейросеть нашла меньше, чем просили — добираем эвристикой
            if len(clips) < count:
                extra, _ = fallback()
                for cand in extra:
                    if len(clips) >= count:
                        break
                    if all(cand["end"] + min_gap_sec < c["start"]
                           or cand["start"] > c["end"] + min_gap_sec for c in clips):
                        clips.append(cand)
                clips.sort(key=lambda c: c["score"], reverse=True)
            return clips, "ollama"
    except Exception as exc:
        print(f"  Ollama не ответила ({type(exc).__name__}), использую эвристику")
    return fallback()
