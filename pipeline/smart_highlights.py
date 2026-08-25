"""Умный отбор моментов через локальную нейросеть (Ollama).

Нейросеть читает расшифровку и сама решает, какие фрагменты самые цепляющие
и где по смыслу закончить клип. Если Ollama не запущена или ответ кривой —
тихо откатываемся на эвристику из highlights.py.
"""
import json
import urllib.request

from .highlights import _split_sentences, pick_highlights

OLLAMA_URL = "http://localhost:11434"


def _ollama_chat(model: str, prompt: str, timeout: float = 180.0) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
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


def ollama_available(model: str) -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        return any(n == model or n.startswith(model + ":") or n.split(":")[0] == model.split(":")[0]
                   for n in names)
    except Exception:
        return False


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
            start = max(sentences[i]["start"] - 0.2, 0.0)
            end = min(sentences[j]["end"] + 0.35, total_duration)
            dur = end - start
            if dur < min_sec * 0.6 or dur > max_sec * 1.3:
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
            return clips[:count], "ollama"
    except Exception as exc:
        print(f"  Ollama не ответила ({type(exc).__name__}), использую эвристику")
    return fallback()
