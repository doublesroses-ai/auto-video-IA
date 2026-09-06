"""Чтение текста с экрана: реплики и сообщения, которые появляются в кадре.

Распознаёт встроенный в Windows OCR (Windows.Media.Ocr). Он мгновенный,
не качает моделей и не требует видеокарты; русский и английский знает
из коробки — нужен только языковой пакет Windows, на русской системе он есть.

Сложность не в распознавании, а в отборе: на экране игры текста полно —
меню, кнопки, счётчики. Сообщением считается текст, который появился
на время и исчез; то, что висит в кадре почти весь ролик, — интерфейс.
"""
import asyncio
import difflib
import re
import shutil
from collections import Counter
from pathlib import Path

from .ffmpeg_utils import ffmpeg, run

FPS = 2.0             # кадров в секунду на распознавание
STATIC_SHARE = 0.6    # текст, что суммарно в кадре 60% ролика, — интерфейс, не сообщение
MIN_WORDS = 2
MIN_LETTERS = 6
MATCH_RATIO = 0.8     # насколько похожи тексты, чтобы считаться одним и тем же
LOST_SEC = 1.6        # сколько можно не видеть текст, не закрывая его
REPEAT_WINDOW = 10.0  # одинаковый текст ближе этого не читаем второй раз

_WORD = re.compile(r"[А-Яа-яЁёA-Za-z]{2,}")
_LETTER = re.compile(r"[А-Яа-яЁёA-Za-z]")
_NOISE = re.compile(r"[^А-Яа-яЁёA-Za-z0-9]+")


def ocr_available() -> tuple[bool, str]:
    """(можно ли распознавать, язык или причина отказа)."""
    try:
        from winrt.windows.media.ocr import OcrEngine
    except ImportError:
        return False, ("не установлены библиотеки Windows OCR "
                       "(pip install -r requirements.txt)")
    tags = [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
    if not tags:
        return False, ("в Windows нет языков для распознавания текста: "
                       "Параметры → Время и язык → Язык → добавить русский")
    for tag in tags:
        if tag.lower().startswith("ru"):
            return True, tag
    return True, tags[0]


def _engine(tag: str):
    from winrt.windows.globalization import Language
    from winrt.windows.media.ocr import OcrEngine
    engine = OcrEngine.try_create_from_language(Language(tag))
    if engine is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise RuntimeError("Windows не смогла создать распознаватель текста")
    return engine


async def _recognize_all(paths: list[Path], tag: str) -> list[list[dict]]:
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.storage import StorageFile

    engine = _engine(tag)
    out = []
    for path in paths:
        # WinRT принимает только абсолютный путь, относительный — «слишком длинный»
        file = await StorageFile.get_file_from_path_async(str(Path(path).resolve()))
        stream = await file.open_read_async()
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            rects = [w.bounding_rect for w in line.words]
            if not rects:
                continue
            x0 = min(r.x for r in rects)
            y0 = min(r.y for r in rects)
            x1 = max(r.x + r.width for r in rects)
            y1 = max(r.y + r.height for r in rects)
            lines.append({"text": line.text.strip(), "x": x0, "y": y0,
                          "w": x1 - x0, "h": y1 - y0})
        out.append(lines)
        stream.close()
    return out


def _group_blocks(lines: list[dict]) -> list[dict]:
    """Склеивает соседние строки одного размера в абзац (реплика в облачке
    обычно занимает две-три строки)."""
    blocks: list[dict] = []
    for line in sorted(lines, key=lambda l: (l["y"], l["x"])):
        for b in blocks:
            gap = line["y"] - (b["y"] + b["h"])
            h = max(line["h"], b["line_h"])
            same_size = 0.6 < line["h"] / max(b["line_h"], 1) < 1.7
            near_x = line["x"] < b["x"] + b["w"] + h and line["x"] + line["w"] > b["x"] - h
            if -0.3 * h <= gap <= 0.9 * h and same_size and near_x:
                b["text"] += " " + line["text"]
                b["w"] = max(b["x"] + b["w"], line["x"] + line["w"]) - min(b["x"], line["x"])
                b["x"] = min(b["x"], line["x"])
                b["h"] = line["y"] + line["h"] - b["y"]
                b["line_h"] = line["h"]
                break
        else:
            blocks.append({**line, "line_h": line["h"]})
    return blocks


def _is_message(text: str) -> bool:
    """Похоже ли на фразу, а не на кнопку, таблицу, число или обрывок."""
    words = _WORD.findall(text)
    letters = len(_LETTER.findall(text))
    digits = len(re.findall(r"\d", text))
    dense = len(text.replace(" ", "")) or 1
    if len(words) < MIN_WORDS or letters < MIN_LETTERS:
        return False
    if letters / dense < 0.55 or digits > letters / 3:
        return False                      # таблицы, счётчики, координаты
    avg = sum(len(w) for w in words) / len(words)
    if avg < 3.5:
        return False                      # «Пла нет Ы» — осколки, не слова
    # Фраза, а не подпись: есть знак конца предложения после буквы, кавычки
    # или хотя бы шесть слов. «Карта космоса» и «Большой бур» — подписи.
    return bool(_SENTENCE.search(text)) or len(words) >= 6


_SENTENCE = re.compile(r"[А-Яа-яЁёA-Za-z][.!?…]|[«»\"]")


READ_SPEED = 14.0     # символов в секунду — примерно так читает зритель и диктор


def _readable(text: str, visible: float) -> bool:
    """Успел бы зритель прочитать этот текст за время показа.

    Подсказка на 250 символов, мелькнувшая на секунду, — не сообщение,
    а всплывающая справка под курсором; диктору её читать не надо.
    """
    return len(text) / READ_SPEED <= visible * 2.5 + 1.5


def _norm(text: str) -> str:
    return _NOISE.sub("", text.lower().replace("ё", "е"))


def _same_text(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a.startswith(b) or b.startswith(a):   # текст печатается по буквам
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= MATCH_RATIO


def _track(frames: list[tuple[float, list[dict]]], frame_h: int) -> list[dict]:
    """Прослеживает каждый блок текста по кадрам: когда появился, когда пропал."""
    tracks: list[dict] = []
    for t, blocks in frames:
        for b in blocks:
            if not _is_message(b["text"]):
                continue
            norm = _norm(b["text"])
            yc = b["y"] + b["h"] / 2
            for tr in tracks:
                if t - tr["last"] > LOST_SEC:
                    continue
                if abs(tr["yc"] - yc) > 0.06 * frame_h:
                    continue
                if _same_text(tr["norm"], norm):
                    tr["last"] = t
                    tr["frames"] += 1
                    tr["variants"][b["text"]] += 1
                    if len(norm) > len(tr["norm"]):
                        tr["norm"] = norm
                    tr["yc"] = yc
                    break
            else:
                tracks.append({"first": t, "last": t, "frames": 1, "yc": yc,
                               "norm": norm, "variants": Counter({b["text"]: 1})})
    return tracks


def _best_variant(variants: Counter) -> str:
    # самый частый вариант распознавания; при равенстве — самый длинный
    return max(variants.items(), key=lambda kv: (kv[1], len(kv[0])))[0]


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(\w)- (\w)", r"\1\2", text)   # перенос слова со строки
    return text.strip(" -–—|")


def read_screen_text(video: str | Path, pieces: list[list[float]],
                     work_dir: Path, fps: float = FPS) -> list[dict]:
    """Возвращает сообщения с экрана: [{start, end, text}] во времени клипа."""
    ok, tag = ocr_available()
    if not ok:
        raise RuntimeError(f"Чтение текста с экрана недоступно: {tag}")

    frames_dir = Path(work_dir) / "ocr_frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    paths: list[Path] = []
    times: list[float] = []
    offset = 0.0
    for k, (a, b) in enumerate(pieces):
        dur = float(b) - float(a)
        pattern = frames_dir / f"p{k}_%04d.jpg"
        run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
             "-ss", f"{float(a):.3f}", "-t", f"{dur:.3f}", "-i", str(video),
             "-vf", f"fps={fps:g}", "-q:v", "3", str(pattern)],
            desc="кадры для чтения текста")
        found = sorted(frames_dir.glob(f"p{k}_*.jpg"))
        for n, p in enumerate(found):
            paths.append(p)
            times.append(offset + n / fps)
        offset += dur
    total = offset
    if not paths:
        return []

    frame_h = 0
    try:
        from PIL import Image
        with Image.open(paths[0]) as im:
            frame_h = im.height
    except Exception:
        pass
    per_frame = asyncio.run(_recognize_all(paths, tag))
    if not frame_h:
        frame_h = max((l["y"] + l["h"] for lines in per_frame for l in lines), default=1080)

    frames = [(t, _group_blocks(lines)) for t, lines in zip(times, per_frame)]
    tracks = _track(frames, frame_h)
    shutil.rmtree(frames_dir, ignore_errors=True)

    # Интерфейс определяем по суммарному времени на экране: кнопка, которую
    # на восемь секунд закрыло окно, всё равно висит в кадре почти весь ролик.
    seen_total: dict[str, float] = {}
    for tr in tracks:
        key = next((k for k in seen_total if _same_text(k, tr["norm"])), tr["norm"])
        seen_total[key] = seen_total.get(key, 0.0) + (tr["last"] - tr["first"] + 1 / fps)
    static = {k for k, s in seen_total.items() if s >= STATIC_SHARE * total}

    messages = []
    for tr in sorted(tracks, key=lambda x: x["first"]):
        if tr["frames"] < 2:
            continue                                     # мелькнуло — скорее шум
        if any(_same_text(k, tr["norm"]) for k in static):
            continue                                     # интерфейс
        text = _clean(_best_variant(tr["variants"]))
        visible = tr["last"] - tr["first"] + 1 / fps
        if not _readable(text, visible):
            continue
        if any(_same_text(m["norm"], tr["norm"]) and tr["first"] - m["start"] < REPEAT_WINDOW
               for m in messages):
            continue                                     # тот же текст повторно
        messages.append({"start": round(tr["first"], 2),
                         "end": round(min(tr["last"] + 1 / fps, total), 2),
                         "text": text, "norm": tr["norm"]})
    for m in messages:
        m.pop("norm", None)
    return messages
