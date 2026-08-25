"""Восстановление пунктуации в расшифровке (Silero TE, локально, бесплатно).

Whisper часто не ставит точки в разговорной речи — без них плохо работают
и выбор моментов, и субтитры. Модель Silero расставляет знаки и заглавные.
"""
import re

_apply_te = None
_SENT_PUNCT = re.compile(r"[.!?…]")


def _load_model():
    """Ленивая загрузка модели (первый раз скачивается ~100 МБ, потом из кэша)."""
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


def _needs_punctuation(segments: list[dict]) -> bool:
    """Пунктуация нужна, если реже чем в каждом четвёртом сегменте есть знаки."""
    if not segments:
        return False
    with_punct = sum(1 for s in segments if _SENT_PUNCT.search(s["text"]))
    return with_punct / len(segments) < 0.25


def _chunks(words: list[dict], size: int = 80):
    for i in range(0, len(words), size):
        yield words[i:i + size]


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
    for chunk in _chunks(all_words):
        text = " ".join(w["word"].strip(".,!?…:;") for w in chunk)
        try:
            enhanced = apply_te(text.lower(), lan=lang)
        except Exception:
            continue
        tokens = enhanced.split()
        if len(tokens) != len(chunk):
            continue  # модель слила/разбила слова — этот кусок не трогаем
        for w, tok in zip(chunk, tokens):
            if w["word"] != tok:
                w["word"] = tok
                changed += 1

    # пересобираем тексты сегментов из обновлённых слов
    for seg in segments:
        if seg.get("words"):
            seg["text"] = " ".join(w["word"] for w in seg["words"])
    print(f"  Пунктуация восстановлена (обновлено слов: {changed})")
    return changed > 0
