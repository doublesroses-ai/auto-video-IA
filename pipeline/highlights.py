"""Выбор самых цепляющих фрагментов расшифровки для шортсов (эвристика)."""
import re

# слова-крючки, которые обычно удерживают внимание
_HOOKS = [
    # русские
    "как", "почему", "зачем", "секрет", "ошибк", "деньг", "лайфхак", "никогда",
    "самый", "самая", "самое", "топ", "важно", "главн", "представ", "бесплатно",
    "прост", "факт", "никто", "на самом деле", "все думают", "проблем", "способ",
    "внимание", "запомни", "честно", "правда", "истори", "случил", "оказалось",
    # английские
    "how", "why", "secret", "mistake", "money", "never", "best", "top",
    "important", "imagine", "actually", "free", "easy", "fact", "nobody",
    "problem", "story", "truth", "warning", "remember",
]

_SENTENCE_END = re.compile(r"[.!?…]+[\"»')]*\s*$")


MAX_SENTENCE_SEC = 15.0
MAX_GAP_SEC = 1.0


def _split_sentences(segments: list[dict]) -> list[dict]:
    """Склеивает сегменты Whisper в предложения.

    Разбивает по знакам конца предложения, а если их нет (частая история
    с разговорной речью) — принудительно по паузам и длительности.
    """
    sentences = []
    current_words: list[dict] = []
    current_text: list[str] = []
    start = None
    prev_end = None

    def flush(end_time):
        nonlocal current_words, current_text, start
        text = " ".join(current_text).strip()
        if text and start is not None:
            sentences.append({
                "start": start, "end": end_time, "text": text,
                "n_words": len(current_words) or len(text.split()),
            })
        current_words, current_text, start = [], [], None

    for seg in segments:
        # длинная пауза между сегментами — граница мысли
        if start is not None and prev_end is not None and \
                seg["start"] - prev_end > MAX_GAP_SEC:
            flush(prev_end)
        if start is None:
            start = seg["start"]
        current_text.append(seg["text"])
        current_words.extend(seg.get("words", []))
        prev_end = seg["end"]
        if _SENTENCE_END.search(seg["text"]) or seg["end"] - start > MAX_SENTENCE_SEC:
            flush(seg["end"])
    if current_text and segments:
        flush(segments[-1]["end"])
    return sentences


def _score_sentence(s: dict) -> float:
    text = s["text"].lower()
    score = 0.0
    score += 2.0 * sum(1 for h in _HOOKS if h in text)
    if "?" in s["text"]:
        score += 2.0
    if "!" in s["text"]:
        score += 0.5
    if re.search(r"\d", s["text"]):
        score += 1.0
    dur = max(s["end"] - s["start"], 0.5)
    score += min(s["n_words"] / dur, 4.0) * 0.5  # плотность речи
    return score


def pick_highlights(transcript: dict, total_duration: float, count: int,
                    min_sec: float, max_sec: float, min_gap_sec: float) -> list[dict]:
    """Возвращает список клипов [{start, end, hook, score}] по убыванию оценки."""
    sentences = _split_sentences(transcript.get("segments", []))

    # если речи нет — режем видео на равные куски
    if not sentences:
        clips = []
        target = (min_sec + max_sec) / 2
        n = max(1, min(count, int(total_duration // target)))
        for i in range(n):
            start = i * (total_duration / n)
            clips.append({
                "start": round(start, 2),
                "end": round(min(start + target, total_duration), 2),
                "hook": "", "score": 0.0,
            })
        return clips

    scores = [_score_sentence(s) for s in sentences]

    # окна из подряд идущих предложений длиной min..max секунд
    windows = []
    for i in range(len(sentences)):
        j = i
        while j < len(sentences) and sentences[j]["end"] - sentences[i]["start"] < max_sec:
            j += 1
        j = max(j, i + 1)
        end = min(sentences[j - 1]["end"], sentences[i]["start"] + max_sec)
        dur = end - sentences[i]["start"]
        if dur < min_sec:
            end = min(sentences[i]["start"] + min_sec, total_duration)
            dur = end - sentences[i]["start"]
        if dur < min_sec * 0.6:
            continue
        window_score = sum(scores[i:j]) / max(dur / 30.0, 1.0)
        window_score += _score_sentence(sentences[i]) * 0.5  # сильное первое предложение
        windows.append({
            "start": round(max(sentences[i]["start"] - 0.2, 0.0), 2),
            "end": round(end + 0.2, 2),
            "hook": sentences[i]["text"][:120],
            "score": round(window_score, 2),
        })

    # жадный отбор без пересечений и слишком близких соседей
    windows.sort(key=lambda w: w["score"], reverse=True)
    chosen: list[dict] = []
    for w in windows:
        if len(chosen) >= count:
            break
        ok = all(
            w["end"] + min_gap_sec < c["start"] or w["start"] > c["end"] + min_gap_sec
            for c in chosen
        )
        if ok:
            chosen.append(w)
    return chosen
