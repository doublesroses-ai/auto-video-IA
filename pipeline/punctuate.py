"""Восстановление пунктуации в расшифровке (Silero TE, локально, бесплатно).

Whisper часто не ставит точки в разговорной речи — без них плохо работают
и выбор моментов, и субтитры. Модель Silero расставляет знаки и заглавные.

Тонкость: модель может склеить или разбить слова, поэтому переносим из её ответа
только знаки препинания и заглавные буквы, сохраняя исходный список слов
с их таймкодами (иначе развалится караоке-подсветка в субтитрах).
"""
import re

_apply_te = None
_SENT_PUNCT = re.compile(r"[.!?…]")
_NON_WORD = re.compile(r"[^\w]", re.UNICODE)
_TAIL_PUNCT = re.compile(r"[^\w]+$", re.UNICODE)


def _load_model():
    """Ленивая загрузка модели (первый раз скачивается ~90 МБ, потом из кэша)."""
    global _apply_te
    if _apply_te is not None:
        return _apply_te
    import torch
    torch.set_num_threads(4)
    _, _, _, _, apply_te = torch.hub.load(
        repo_or_dir="snakers4/silero-models", model="silero_te",
        trust_repo=True, verbose=False,
    )
    _apply_te = apply_te
    return _apply_te


# В живой русской речи предложение — это примерно 8-12 слов. Если знаков конца
# заметно меньше, границы фраз условны, и клипы начинают резаться посреди мысли.
WORDS_PER_SENTENCE_LIMIT = 15
PUNCT_CHUNK_WORDS = 20


def _needs_punctuation(segments: list[dict]) -> bool:
    """Хватает ли в расшифровке знаков конца предложения.

    Считаем знаки на слово, а не долю сегментов с точкой. Сегменты Whisper
    длинные: одна точка в конце тридцатисловного куска помечала его как
    «с пунктуацией», хотя внутри границ не было вовсе. Из-за этого
    восстановление молча пропускалось, и потом лишь 7% фраз оказывались
    законченными — клипы обрывались с обоих концов.
    """
    words = sum(len(s.get("words", [])) or len(s["text"].split()) for s in segments)
    if words < 30:
        return False
    marks = sum(len(_SENT_PUNCT.findall(s["text"])) for s in segments)
    return marks == 0 or words / marks > WORDS_PER_SENTENCE_LIMIT


def _norm(s: str) -> str:
    return _NON_WORD.sub("", s).lower()


def _tail_punct(token: str) -> str:
    m = _TAIL_PUNCT.search(token)
    return m.group(0) if m else ""


def _transfer(words: list[dict], tokens: list[str]) -> int:
    """Переносит знаки и заглавные из tokens в words. Возвращает число изменений.

    Идёт по обоим спискам одновременно, накапливая буквы, пока накопленные строки
    не совпадут — так переживаем и склейку («кто то» → «кто-то»),
    и разбиение слов моделью.
    """
    changed = 0
    oi = ti = 0
    n_o, n_t = len(words), len(tokens)

    while oi < n_o and ti < n_t:
        o_end, t_end = oi, ti
        o_acc, t_acc = _norm(words[oi]["word"]), _norm(tokens[ti])

        # выравниваем группы, пока накопленные буквы не совпадут
        guard = 0
        while o_acc != t_acc and guard < 12:
            guard += 1
            if len(o_acc) < len(t_acc):
                if o_end + 1 >= n_o:
                    break
                o_end += 1
                o_acc += _norm(words[o_end]["word"])
            else:
                if t_end + 1 >= n_t:
                    break
                t_end += 1
                t_acc += _norm(tokens[t_end])

        if o_acc != t_acc:
            # Рассинхрон: ищем ближайшее слово, на котором списки снова сходятся.
            # Простой сдвиг на одно слово в каждом списке разваливал разбор
            # до конца куска, и знаки не переносились вовсе.
            resync = None
            for step in range(1, 6):
                if oi + step < n_o and ti + step < n_t and \
                        _norm(words[oi + step]["word"]) == _norm(tokens[ti + step]):
                    resync = step
                    break
            if resync:
                oi += resync
                ti += resync
                continue

        if o_acc and o_acc == t_acc:
            # заглавная буква — от первого токена группы
            first_tok = tokens[ti].lstrip("«\"'(")
            if first_tok[:1].isupper():
                w = words[oi]["word"]
                if w[:1].islower():
                    words[oi]["word"] = w[:1].upper() + w[1:]
                    changed += 1
            # знак препинания — последнему слову группы
            punct = _tail_punct(tokens[t_end])
            if punct:
                w = words[o_end]["word"]
                if not _TAIL_PUNCT.search(w):
                    words[o_end]["word"] = w + punct
                    changed += 1
            oi, ti = o_end + 1, t_end + 1
        else:
            # рассинхрон — сдвигаемся на слово и пробуем снова
            oi += 1
            ti += 1

    return changed


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def restore_punctuation(transcript: dict, language: str | None = None) -> bool:
    """Расставляет знаки препинания в transcript (правит на месте).

    Возвращает True, если пунктуация была применена.
    """
    lang = (language or transcript.get("language") or "").lower()
    if lang not in ("ru", "en", "de", "es"):
        return False
    segments = transcript.get("segments", [])
    if not _needs_punctuation(segments):
        return False

    try:
        apply_te = _load_model()
    except Exception as exc:
        print(f"  Пунктуация недоступна ({type(exc).__name__}), пропускаю")
        return False

    all_words = [w for seg in segments for w in seg.get("words", [])]
    if not all_words:
        return False

    changed = 0
    # 20 слов, а не 60: на длинных кусках модель сбивает соответствие
    # слов, и знаки не переносятся вовсе (замерено: 0 правок против 18)
    for chunk in _chunks(all_words, PUNCT_CHUNK_WORDS):
        text = " ".join(_NON_WORD.sub("", w["word"]) or w["word"] for w in chunk)
        if not text.strip():
            continue
        try:
            enhanced = apply_te(text.lower(), lan=lang)
        except Exception:
            continue
        changed += _transfer(chunk, enhanced.split())

    # пересобираем тексты сегментов из обновлённых слов
    for seg in segments:
        if seg.get("words"):
            seg["text"] = " ".join(w["word"] for w in seg["words"])

    pct = round(100 * changed / max(len(all_words), 1))
    print(f"  Пунктуация восстановлена (правок: {changed}, ~{pct}% слов)")
    return changed > 0
