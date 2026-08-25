"""Озвучка текста нейроголосом (edge-tts) с таймкодами слов для субтитров."""
import asyncio
from pathlib import Path

VOICES = {
    "Светлана (жен.)": "ru-RU-SvetlanaNeural",
    "Дмитрий (муж.)": "ru-RU-DmitryNeural",
}


async def _synth_async(text: str, voice: str, rate: str, out_mp3: str) -> list[dict]:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice, rate=rate)
    words = []
    with open(out_mp3, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7  # 100-нс тики → секунды
                words.append({
                    "start": round(start, 3),
                    "end": round(start + chunk["duration"] / 1e7, 3),
                    "word": chunk["text"].strip(),
                })
    return [w for w in words if w["word"]]


def _group_segments(words: list[dict]) -> list[dict]:
    """Группирует слова в сегменты по паузам и длине (для ASS и редактора)."""
    segments = []
    current: list[dict] = []
    for w in words:
        if current and (w["start"] - current[-1]["end"] > 0.6 or len(current) >= 12):
            segments.append(current)
            current = []
        current.append(w)
    if current:
        segments.append(current)
    return [
        {
            "start": seg[0]["start"],
            "end": seg[-1]["end"],
            "text": " ".join(w["word"] for w in seg),
            "words": seg,
        }
        for seg in segments
    ]


def synthesize(text: str, voice: str, rate: str, out_mp3: str | Path) -> dict:
    """Озвучивает текст, возвращает transcript-словарь как у Whisper.

    Требует интернет (голоса Microsoft Edge). При ошибке сети бросает исключение.
    """
    text = " ".join(text.split())
    if not text:
        raise ValueError("Пустой текст — озвучивать нечего")
    words = asyncio.run(_synth_async(text, voice, rate, str(out_mp3)))
    if not words:
        raise RuntimeError("Озвучка не удалась: нет ни звука, ни таймкодов "
                           "(проверь интернет-соединение)")
    return {"language": "ru", "segments": _group_segments(words)}
