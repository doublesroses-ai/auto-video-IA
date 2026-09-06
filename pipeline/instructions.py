"""Правки словами: человек пишет, что поправить в ролике, нейросеть (Ollama)
переводит просьбу в список операций, которые умеет выполнять edits.py.

Модель маленькая и ошибается, поэтому её ответ не исполняется вслепую:
он проверяется кодом (времена в пределах ролика, известные операции),
переводится обратно в понятные слова и показывается человеку на подтверждение.
"""
import json
import re
from pathlib import Path

from .edits import OPS, STYLES, PLACES, describe, DEFAULT_OUTRO
from .smart_highlights import ollama_available, _ollama_chat

ALL_OPS = OPS + ("narrate", "title")


def timeline(transcript: dict, pieces: list[list[float]]) -> list[str]:
    """Что и когда говорится в ролике — во времени самого ролика."""
    out = []
    offset = 0.0
    for a, b in pieces:
        a, b = float(a), float(b)
        for seg in transcript.get("segments", []):
            if seg["end"] <= a or seg["start"] >= b:
                continue
            s = offset + max(seg["start"], a) - a
            e = offset + min(seg["end"], b) - a
            out.append(f"[{_mmss(s)}–{_mmss(e)}] {seg['text'].strip()}")
        offset += b - a
    return out


def _mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _prompt(duration: float, lines: list[str], request: str) -> str:
    speech = "\n".join(lines) if lines else "(речи в ролике нет)"
    return f"""Ты помощник видеомонтажёра. Человек написал, что поправить в коротком ролике.
Переведи просьбу в список операций. Делай только то, о чём просят; ничего не добавляй от себя.
Время указывай в секундах от начала ролика (число). Длительность ролика: {duration:.1f} с.

Что говорится в ролике (время — текст):
{speech}

Операции (все поля обязательны):
- вырезать фрагмент: {{"op":"cut","from":секунды,"to":секунды}}
- оставить только отрезок, остальное убрать: {{"op":"trim","start":секунды,"end":секунды}}
- вставить картинку: {{"op":"image","at":"start" или "end" или секунды,"seconds":сколько показывать,"file":"путь к файлу или пустая строка"}}
- анимация в начале: {{"op":"intro","style":"card" (заставка с надписью) или "zoom" (наезд камеры) или "fade" (из затемнения),"text":"надпись для заставки или пустая строка"}}
- анимация в финале: {{"op":"outro","style":"card" или "zoom" или "fade","text":"надпись для заставки или пустая строка"}}
- надпись на экране: {{"op":"text","text":"что написать","at":секунды,"seconds":сколько показывать,"where":"top" или "center" или "bottom"}}
- диктор произносит текст голосом: {{"op":"narrate","text":"что сказать","at":секунды}}
- сменить заголовок ролика: {{"op":"title","text":"новый заголовок"}}

Если просят «в конце» без уточнений — это последние секунды ролика; «в начале» — первые.
Если в просьбе есть что-то, чего сделать нельзя этими операциями, не выдумывай замену —
опиши это в поле unclear.

Ответ — только JSON вида {{"ops":[...],"unclear":"что не удалось понять или пустая строка"}}

Просьба: «{request.strip()}»"""


def _to_float(value, default=None):
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"(\d+)[:.](\d{2})(?![\d])", value)
        if m and ":" in value:
            return int(m.group(1)) * 60 + int(m.group(2))
        m = re.search(r"-?\d+(?:[.,]\d+)?", value)
        if m:
            return float(m.group(0).replace(",", "."))
    return default


def normalize(ops: list, duration: float) -> tuple[list[dict], list[str]]:
    """Проверяет ответ модели. Возвращает (годные операции, что отброшено и почему)."""
    good, problems = [], []
    for raw in ops if isinstance(ops, list) else []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("op", "")).strip().lower()
        if kind not in ALL_OPS:
            problems.append(f"неизвестная операция «{kind}»")
            continue
        try:
            good.append(_normalize_one(kind, raw, duration))
        except ValueError as exc:
            problems.append(str(exc))
    return good, problems


def _clamp(t: float, duration: float) -> float:
    return round(min(max(t, 0.0), duration), 2)


def _normalize_one(kind: str, raw: dict, duration: float) -> dict:
    text = str(raw.get("text", "") or "").strip()
    if kind == "cut":
        a, b = _to_float(raw.get("from")), _to_float(raw.get("to"))
        if a is None or b is None:
            raise ValueError("вырезание без времени")
        a, b = sorted((_clamp(a, duration), _clamp(b, duration)))
        if b - a < 0.2:
            raise ValueError("фрагмент для вырезания слишком короткий")
        return {"op": "cut", "from": a, "to": b}
    if kind == "trim":
        a, b = _to_float(raw.get("start"), 0.0), _to_float(raw.get("end"), duration)
        a, b = sorted((_clamp(a, duration), _clamp(b, duration)))
        if b - a < 1.0:
            raise ValueError("после обрезки не осталось бы ролика")
        return {"op": "trim", "start": a, "end": b}
    if kind == "image":
        at = raw.get("at", "start")
        if isinstance(at, str):
            low = at.strip().lower()
            if low in ("start", "begin", "начало", "в начало", "в начале", ""):
                at = "start"
            elif low in ("end", "конец", "в конец", "в конце", "финал"):
                at = "end"
            else:
                at = _to_float(at, 0.0)
        if not isinstance(at, str):
            at = _clamp(float(at), duration)
        seconds = min(max(_to_float(raw.get("seconds"), 3.0) or 3.0, 1.0), 15.0)
        # модель охотно переписывает подсказку «путь к файлу» как путь —
        # верим только существующему файлу, остальное спросим у человека
        file = str(raw.get("file", "") or "").strip()
        if not Path(file).is_file():
            file = ""
        return {"op": "image", "at": at, "seconds": round(seconds, 1), "file": file}
    if kind in ("intro", "outro"):
        style = str(raw.get("style", "card") or "card").strip().lower()
        if style not in STYLES:
            style = "card"
        return {"op": kind, "style": style, "text": text[:80]}
    if kind == "text":
        if not text:
            raise ValueError("надпись без текста")
        where = str(raw.get("where", "center") or "center").strip().lower()
        if where not in PLACES:
            where = "center"
        at = _clamp(_to_float(raw.get("at"), 0.0) or 0.0, duration)
        seconds = min(max(_to_float(raw.get("seconds"), 3.0) or 3.0, 0.8), 30.0)
        return {"op": "text", "text": text[:120], "at": at,
                "seconds": round(seconds, 1), "where": where}
    if kind == "narrate":
        if not text:
            raise ValueError("диктору нечего сказать")
        at = _clamp(_to_float(raw.get("at"), 0.0) or 0.0, duration)
        return {"op": "narrate", "text": text[:400], "at": at}
    if kind == "title":
        if not text:
            raise ValueError("пустой заголовок")
        return {"op": "title", "text": text[:120]}
    raise ValueError(kind)


def parse(model: str, request: str, duration: float, lines: list[str]) -> dict:
    """Просьба → {"ops": [...], "problems": [...], "unclear": str}.

    Бросает RuntimeError, если нейросеть недоступна.
    """
    if not ollama_available(model):
        raise RuntimeError(
            "Нейросеть Ollama не запущена или модель не скачана — правки словами "
            "недоступны. Установить её можно через окно первой настройки.")
    raw = _ollama_chat(model, _prompt(duration, lines, request), num_predict=600,
                       timeout=180)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if m else {}
    if isinstance(data, list):
        data = {"ops": data}
    ops, problems = normalize(data.get("ops", []), duration)
    unclear = str(data.get("unclear", "") or "").strip()
    return {"ops": ops, "problems": problems, "unclear": unclear}


def plan_text(result: dict) -> str:
    """План по-русски для окна подтверждения."""
    lines = [f"{n}. {describe(op)}" for n, op in enumerate(result["ops"], 1)]
    text = "\n".join(lines) if lines else "(ни одной операции)"
    if result.get("unclear"):
        text += f"\n\nНе поняла: {result['unclear']}"
    if result.get("problems"):
        text += "\n\nПропущено: " + "; ".join(result["problems"])
    return text
