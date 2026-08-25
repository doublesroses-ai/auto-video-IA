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

    def flush(end_time, strong):
        nonlocal current_words, current_text, start
        text = " ".join(current_text).strip()
        if text and start is not None:
            sentences.append({
                "start": start, "end": end_time, "text": text,
                "n_words": len(current_words) or len(text.split()),
                # strong: конец подтверждён знаком препинания или большой паузой,
                # то есть фраза действительно закончена по смыслу
                "strong": strong,
            })
        current_words, current_text, start = [], [], None

    for seg in segments:
        # длинная пауза между сегментами — граница мысли
        if start is not None and prev_end is not None and \
                seg["start"] - prev_end > MAX_GAP_SEC:
            flush(prev_end, strong=True)
        if start is None:
            start = seg["start"]
        current_text.append(seg["text"])
        current_words.extend(seg.get("words", []))
        prev_end = seg["end"]
        if _SENTENCE_END.search(seg["text"]):
            flush(seg["end"], strong=True)
        elif seg["end"] - start > MAX_SENTENCE_SEC:
            flush(seg["end"], strong=False)
    if current_text and segments:
        flush(segments[-1]["end"], strong=True)
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

    # Окна из ЦЕЛЫХ фраз: конец клипа — только граница фразы, приоритет у
    # законченных по смыслу (пунктуация или большая пауза). Посреди фразы
    # клип не обрывается никогда.
    windows = []
    for i in range(len(sentences)):
        start = sentences[i]["start"]
        best_j, best_strong = None, False
        j = i
        while j < len(sentences) and sentences[j]["end"] - start <= max_sec:
            if sentences[j]["end"] - start >= min_sec:
                if sentences[j]["strong"]:
                    best_j, best_strong = j, True  # самая длинная законченная фраза
                elif not best_strong:
                    best_j = j
            j += 1
        if best_j is None:
            # в диапазон [min, max] не попала ни одна граница фразы:
            # разрешаем чуть выйти за max, лишь бы закончить фразу
            if j < len(sentences) and sentences[j]["end"] - start <= max_sec * 1.2:
                best_j, best_strong = j, sentences[j]["strong"]
            elif j - 1 > i or (j - 1 == i and sentences[i]["end"] - start >= min_sec * 0.6):
                best_j, best_strong = j - 1, sentences[j - 1]["strong"]
            else:
                continue
        end = sentences[best_j]["end"]
        dur = end - start
        window_score = sum(scores[i:best_j + 1]) / max(dur / 30.0, 1.0)
        window_score += _score_sentence(sentences[i]) * 0.5  # сильное первое предложение
        if best_strong:
            window_score += 2.0  # финал закончен по смыслу
        windows.append({
            "start": round(max(start - 0.2, 0.0), 2),
            "end": round(min(end + 0.35, total_duration), 2),
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
