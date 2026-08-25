"""Генерация стильных ASS-субтитров с подсветкой произносимого слова."""


def _ass_time(t: float) -> str:
    t = max(t, 0.0)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(word: str) -> str:
    return word.replace("{", "").replace("}", "").strip()


def _collect_words(transcript: dict, clip_start: float, clip_end: float) -> list[dict]:
    """Слова в границах клипа со сдвигом времени к нулю."""
    words = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words", []):
            if w["end"] <= clip_start or w["start"] >= clip_end:
                continue
            words.append({
                "start": max(w["start"] - clip_start, 0.0),
                "end": min(w["end"], clip_end) - clip_start,
                "word": _clean(w["word"]),
            })
    return [w for w in words if w["word"] and w["end"] > w["start"]]


def _group_cards(words: list[dict], max_words: int) -> list[list[dict]]:
    """Разбивает слова на карточки: не более max_words, разрыв при длинной паузе."""
    cards: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            ends_sentence = current[-1]["word"][-1:] in ".!?…"
            if len(current) >= max_words or gap > 0.7 or ends_sentence:
                cards.append(current)
                current = []
        current.append(w)
    if current:
        cards.append(current)
    return cards


def build_ass(transcript: dict, clip_start: float, clip_end: float,
              play_w: int, play_h: int, font: str, font_size: int,
              uppercase: bool, max_words: int, bottom_margin_ratio: float) -> str:
    """Возвращает содержимое .ass файла для клипа [clip_start, clip_end]."""
    words = _collect_words(transcript, clip_start, clip_end)
    cards = _group_cards(words, max_words)

    margin_v = int(play_h * bottom_margin_ratio)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{font_size},&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{max(3, font_size // 16)},1,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # PrimaryColour жёлтый, SecondaryColour белый: karaoke-теги \k подсвечивают
    # произносимое слово жёлтым, остальные остаются белыми.
    lines = []
    for card in cards:
        start = card[0]["start"]
        end = max(card[-1]["end"], start + 0.35)
        parts = []
        cursor = start
        for w in card:
            # \k длится в сотых секунды от конца предыдущего слова
            dur_cs = max(int(round((w["end"] - cursor) * 100)), 1)
            cursor = w["end"]
            text = w["word"].upper() if uppercase else w["word"]
            parts.append(f"{{\\k{dur_cs}}}{text}")
        line = " ".join(parts)
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{line}"
        )
    return header + "\n".join(lines) + "\n"
