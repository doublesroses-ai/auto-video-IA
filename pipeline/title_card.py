"""Заголовок ролика: красивый титр поверх первых секунд шортса.

Титр рисуется тем же ASS, что и субтитры, поэтому здесь только две вещи:
строка стиля и строки Dialogue. Их подставляет subtitles.build_ass_pieces.

Главное отличие от прежнего drawtext: ширина текста не угадывается по числу
букв, а считается по реальным метрикам шрифта. Поэтому заголовок гарантированно
влезает в кадр — раньше «Строю термоядерный реактор» обрезался с обеих сторон.
"""

# Ширина символов Arial Black в тысячных долях кегля (снято с ariblk.ttf).
_W_GROUPS = {
    278: "'", 333: "!,-.:;ijl", 334: " ", 389: "()If", 444: "rt", 500: '"–',
    508: "гт", 556: "z", 611: "?svyу", 615: "в", 621: "ь", 624: "я", 642: "к",
    662: "дч", 667: "«»0123456789FJLabcdeghknopquxГабеёопрсхэ", 672: "У",
    678: "з", 688: "З", 690: "ц", 706: "ий", 713: "н", 716: "л",
    722: "EPSTZЕЁРТ", 730: "К", 771: "ъ", 774: "Ь", 778: "ABCDRVXYАБВСХ",
    780: "Я", 796: "Э", 804: "ЛЧ", 833: "GHKNOQUИЙНОП", 838: "Д", 846: "м",
    860: "Ц", 909: "ы", 924: "Фж", 944: "MwМ", 949: "ш", 962: "щ", 975: "ю",
    1000: "%—…Wm", 1002: "Ъ", 1013: "ф", 1082: "Ы", 1086: "Ж", 1110: "Ш",
    1138: "Щ", 1158: "Ю",
}
_CHAR_WIDTH = {ch: w for w, chars in _W_GROUPS.items() for ch in chars}
_UNKNOWN_WIDTH = 900          # запас для символа, которого нет в таблице

# В ASS Fontsize — это высота строки (ascender+descender), а не кегль em.
# У Arial Black ascender+descender = 1.411 em, значит em = Fontsize * 0.709.
# Отсюда же берётся шаг строк: он равен ровно Fontsize (проверено рендером).
_EM_PER_SIZE = 0.709
_SAFETY = 1.03                # 3% на округления libass при растеризации

TITLE_SECONDS = 2.8           # сколько держится заголовок
SIDE_MARGIN = 70              # поля кадра
PLATE_PAD_X = 34              # отступы плашки от текста
PLATE_PAD_Y = 22
PLATE_RADIUS = 26
MAX_LINES = 3
SIZES = (120, 112, 104, 96, 88, 80, 72, 64, 56, 48, 40)

ACCENT = "&H00D7FF&"          # BGR: янтарный, тот же акцент, что в субтитрах
PLAIN = "&HFFFFFF&"

_STOP_WORDS = {
    "и", "а", "но", "в", "на", "с", "по", "за", "у", "о", "к", "из", "что",
    "как", "это", "не", "же", "бы", "то", "он", "она", "они", "мы", "вы",
    "почему", "когда", "где", "чтобы", "для", "был", "была", "было", "были",
    "мой", "моя", "моё", "свой", "этот", "эта", "весь", "вся", "очень",
}


def text_width(text: str, size: float, font: str = "Arial Black") -> float:
    """Ширина строки в пикселях при ASS-Fontsize=size."""
    units = sum(_CHAR_WIDTH.get(ch, _UNKNOWN_WIDTH) for ch in text)
    extra = 1.0 if font.lower() == "arial black" else 1.12   # чужой шрифт — с запасом
    return units * size * _EM_PER_SIZE * _SAFETY * extra / 1000.0


def _hard_split(word: str, size: float, usable: float, font: str) -> list[str]:
    """Рвёт слово, которое само по себе шире кадра (немецкие сложносоставные и т.п.)."""
    out, cur = [], ""
    for ch in word:
        if cur and text_width(cur + ch, size, font) > usable:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _layout(words: list[str], size: float, usable: float,
            max_lines: int, font: str) -> list[str] | None:
    """Раскладывает слова по строкам: сначала меньше строк, потом ровнее по ширине."""
    n = len(words)
    best: tuple[tuple[int, float], list[str]] | None = None

    def walk(i: int, left: int, acc: list[str]) -> None:
        nonlocal best
        if i == n:
            widest = max(text_width(line, size, font) for line in acc)
            key = (len(acc), widest)
            if best is None or key < best[0]:
                best = (key, list(acc))
            return
        if left == 0:
            return
        line = ""
        for j in range(i, n):
            line = words[j] if not line else f"{line} {words[j]}"
            if text_width(line, size, font) > usable:
                break
            acc.append(line)
            walk(j + 1, left - 1, acc)
            acc.pop()

    walk(0, max_lines, [])
    return best[1] if best else None


def fit(text: str, usable: float, font: str = "Arial Black",
        max_lines: int = MAX_LINES) -> tuple[int, list[str]]:
    """Подбирает самый крупный кегль, при котором текст влезает в max_lines строк."""
    words = text.split()
    for size in SIZES:
        lines = _layout(words, size, usable, max_lines, font)
        if lines:
            return size, lines
    # Не влезло даже минимальным кеглем — значит, в тексте есть слово шире кадра.
    size = SIZES[-1]
    pieces: list[str] = []
    for word in words:
        pieces.extend(_hard_split(word, size, usable, font)
                      if text_width(word, size, font) > usable else [word])
    return size, _layout(pieces, size, usable, max_lines + 3, font) or [" ".join(pieces)]


def accent_index(words: list[str]) -> int:
    """Ключевое слово — самое длинное значимое. Его подсвечиваем цветом."""
    if len(words) < 2:
        return -1                 # выделять единственное слово незачем
    best_len, best_i = 0, -1
    for i, word in enumerate(words):
        core = word.strip(".,!?«»\"'—:;").lower()
        if len(core) < 5 or core in _STOP_WORDS:
            continue
        if len(core) > best_len:
            best_len, best_i = len(core), i
    return best_i


def escape(text: str) -> str:
    """Убирает из текста то, что ASS примет за разметку."""
    return (text.replace("\\", "").replace("{", "").replace("}", "")
                .replace("\n", " ").strip())


def _rounded_rect(x1: int, y1: int, x2: int, y2: int, r: int) -> str:
    """Прямоугольник со скруглёнными углами в координатах ASS-drawing."""
    return (f"m {x1 + r} {y1} l {x2 - r} {y1} b {x2} {y1} {x2} {y1} {x2} {y1 + r} "
            f"l {x2} {y2 - r} b {x2} {y2} {x2} {y2} {x2 - r} {y2} "
            f"l {x1 + r} {y2} b {x1} {y2} {x1} {y2} {x1} {y2 - r} "
            f"l {x1} {y1 + r} b {x1} {y1} {x1} {y1} {x1 + r} {y1}")


def _ass_time(t: float) -> str:
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def title_block(title: str, play_w: int, play_h: int, font: str,
                seconds: float = TITLE_SECONDS,
                uppercase: bool = True) -> tuple[list[str], list[str]]:
    """Строки стилей и строки Dialogue для заголовка.

    Возвращает ([Style: ...], [Dialogue: ...]). Пустые списки, если титра нет.
    """
    title = escape(title)
    if not title:
        return [], []
    if uppercase:
        title = title.upper()

    margin_v = int(play_h * 0.058)                     # верх блока
    usable = play_w - 2 * SIDE_MARGIN - 2 * PLATE_PAD_X
    size, lines = fit(title, usable, font)
    widest = max(text_width(line, size, font) for line in lines)

    # Плашка: блок текста начинается ровно на margin_v, шаг строк равен size.
    centre = play_w // 2
    x1 = round(centre - widest / 2) - PLATE_PAD_X
    x2 = round(centre + widest / 2) + PLATE_PAD_X
    y1 = margin_v - PLATE_PAD_Y
    y2 = margin_v + len(lines) * size + PLATE_PAD_Y

    accent_at = accent_index(title.split())
    rendered, counter = [], 0
    for line in lines:
        parts = []
        for word in line.split():
            if counter == accent_at:
                parts.append(f"{{\\c{ACCENT}}}{word}{{\\c{PLAIN}}}")
            else:
                parts.append(word)
            counter += 1
        rendered.append(" ".join(parts))
    text = r"\N".join(rendered)

    outline = max(4, round(size * 0.055))
    styles = [
        # Плашка — отдельный «шрифтовой» стиль, текста в нём нет, только рисунок.
        "Style: Plate,Arial,20,&H00000000,&H00000000,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        f"Style: Title,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00101010,&HA0000000,"
        f"-1,0,0,0,100,100,0,0,1,{outline},3,8,{SIDE_MARGIN},{SIDE_MARGIN},{margin_v},1",
    ]
    end = _ass_time(seconds)
    events = [
        f"Dialogue: 0,{_ass_time(0)},{end},Plate,,0,0,0,,"
        r"{\fad(260,320)\pos(0,0)\c&H141414&\alpha&H50&\bord0\shad0\p1}"
        + _rounded_rect(x1, y1, x2, y2, PLATE_RADIUS) + r"{\p0}",
        f"Dialogue: 1,{_ass_time(0)},{end},Title,,0,0,0,,"
        r"{\fad(260,320)}" + text,
    ]
    return styles, events
