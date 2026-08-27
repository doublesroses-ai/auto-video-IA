"""Генерация стильных ASS-субтитров с подсветкой произносимого слова.

Ролик может быть склеен из нескольких кусков исходника, поэтому время каждого
слова пересчитывается: слово из куска k сдвигается на сумму длительностей
предыдущих кусков. Карточка субтитров никогда не пересекает шов — иначе
на склейке текст поедет.
"""


def _ass_time(t: float) -> str:
    t = max(t, 0.0)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clean(word: str) -> str:
    return word.replace("{", "").replace("}", "").strip()


def _collect_words(transcript: dict, pieces: list[list[float]]) -> list[dict]:
    """Слова, целиком попавшие в куски, с пересчётом времени под готовый ролик.

    Берём только слова, которые помещаются в кусок ЦЕЛИКОМ. Раньше захватывались
    и задетые краем — из-за этого в конце каждого ролика вспыхивало обрубленное
    слово на треть секунды.
    """
    words = []
    offset = 0.0
    for pi, (a, b) in enumerate(pieces):
        for seg in transcript.get("segments", []):
            for w in seg.get("words", []):
                if w["start"] < a or w["end"] > b:
                    continue
                text = _clean(w["word"])
                if not text or w["end"] <= w["start"]:
                    continue
                words.append({
                    "start": w["start"] - a + offset,
                    "end": w["end"] - a + offset,
                    "word": text,
                    "piece": pi,
                })
        offset += b - a
    words.sort(key=lambda w: w["start"])
    return words


def _group_cards(words: list[dict], max_words: int) -> list[list[dict]]:
    """Разбивает слова на карточки: не более max_words, разрыв при паузе и на шве."""
    cards: list[list[dict]] = []
    current: list[dict] = []
    for w in words:
        if current:
            gap = w["start"] - current[-1]["end"]
            ends_sentence = current[-1]["word"][-1:] in ".!?…"
            crosses_seam = w["piece"] != current[-1]["piece"]
            if len(current) >= max_words or gap > 0.7 or ends_sentence or crosses_seam:
                cards.append(current)
                current = []
        current.append(w)
    if current:
        cards.append(current)
    return cards


TITLE_SECONDS = 2.8       # сколько держится заголовок в начале ролика
TITLE_SIDE_MARGIN = 70    # отступы по краям, чтобы текст не упирался в рамку


def _title_font_size(text: str, play_w: int) -> int:
    """Размер шрифта под длину заголовка.

    Заголовок должен уложиться в две строки: у Arial Black буква занимает
    примерно 0.62 от кегля, отсюда и считаем. Раньше размер был постоянным,
    и длинный заголовок просто уезжал за края кадра.
    """
    usable = play_w - 2 * TITLE_SIDE_MARGIN
    per_line = max(len(text) / 2, 1)          # целимся в две строки
    size = int(usable / (per_line * 0.62))
    return max(40, min(size, int(play_w * 0.075)))


def _ass_escape(text: str) -> str:
    """Убирает из текста то, что ASS примет за разметку."""
    return (text.replace("\\", "")
                .replace("{", "").replace("}", "")
                .replace("\n", " ").strip())


def build_ass_pieces(transcript: dict, pieces: list[list[float]],
                     play_w: int, play_h: int, font: str, font_size: int,
                     uppercase: bool, max_words: int,
                     bottom_margin_ratio: float, title: str = "") -> str:
    """Содержимое .ass для ролика, склеенного из перечисленных кусков."""
    words = _collect_words(transcript, pieces)
    cards = _group_cards(words, max_words)

    margin_v = int(play_h * bottom_margin_ratio)
    title = _ass_escape(title)
    title_size = _title_font_size(title, play_w) if title else 60
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {play_w}
PlayResY: {play_h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{font_size},&H0000FFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{max(3, font_size // 16)},1,2,60,60,{margin_v},1
Style: Title,{font},{title_size},&H00FFFFFF,&H00FFFFFF,&H00202020,&H00202020,-1,0,0,0,100,100,1,0,1,{max(4, title_size // 12)},3,8,{TITLE_SIDE_MARGIN},{TITLE_SIDE_MARGIN},{int(play_h * 0.10)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # PrimaryColour жёлтый, SecondaryColour белый: karaoke-теги \k подсвечивают
    # произносимое слово жёлтым, остальные остаются белыми.
    lines = []
    if title:
        # Заголовок сверху: плавно проявляется, сам переносится по словам
        # и уходит, не мешая смотреть. Держится в размытом поле над кадром.
        lines.append(
            f"Dialogue: 0,{_ass_time(0)},{_ass_time(TITLE_SECONDS)},Title,,0,0,0,,"
            f"{{\\fad(350,450)}}{title}"
        )
    for card in cards:
        start = card[0]["start"]
        end = card[-1]["end"]
        if end <= start:
            continue
        parts = []
        cursor = start
        for w in card:
            dur_cs = max(int(round((w["end"] - cursor) * 100)), 1)
            cursor = w["end"]
            text = w["word"].upper() if uppercase else w["word"]
            parts.append(f"{{\\k{dur_cs}}}{text}")
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Cap,,0,0,0,,{' '.join(parts)}"
        )
    return header + "\n".join(lines) + "\n"


def build_ass(transcript: dict, clip_start: float, clip_end: float,
              play_w: int, play_h: int, font: str, font_size: int,
              uppercase: bool, max_words: int, bottom_margin_ratio: float) -> str:
    """Содержимое .ass для непрерывного отрезка [clip_start, clip_end]."""
    return build_ass_pieces(transcript, [[clip_start, clip_end]], play_w, play_h,
                            font, font_size, uppercase, max_words, bottom_margin_ratio)
