"""Распознавание речи через faster-whisper с таймкодами слов."""
import json
import os
from pathlib import Path

from .ffmpeg_utils import ffmpeg, run

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _add_cuda_dll_dirs() -> None:
    """Подключает cuBLAS/cuDNN из pip-пакетов nvidia-*, чтобы ctranslate2 нашёл DLL."""
    try:
        import nvidia  # noqa: F401
    except ImportError:
        return
    for base in nvidia.__path__:
        for sub in Path(base).iterdir():
            bin_dir = sub / "bin"
            if bin_dir.is_dir():
                os.add_dll_directory(str(bin_dir))
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def _pick_model(name: str, device: str) -> str:
    if name != "auto":
        return name
    return "large-v3-turbo" if device == "cuda" else "small"


def extract_audio(src: str, wav_path: str) -> None:
    run([ffmpeg(), "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav_path)], desc="извлечение звука")


def _run_whisper(wav: str, model_name: str, device: str, language: str | None,
                 hint: str | None = None) -> dict:
    from faster_whisper import WhisperModel

    compute = "float16" if device == "cuda" else "int8"
    model = WhisperModel(_pick_model(model_name, device), device=device, compute_type=compute)
    segments_iter, info = model.transcribe(
        str(wav), language=language, word_timestamps=True,
        vad_filter=True, beam_size=5, initial_prompt=hint,
    )
    segments = []
    # ошибки CUDA всплывают при итерации, поэтому список собираем здесь же
    for seg in segments_iter:
        words = [
            {"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip()}
            for w in (seg.words or []) if w.word.strip()
        ]
        text = seg.text.strip()
        if text:
            segments.append({
                "start": round(seg.start, 3),
                "end": round(seg.end, 3),
                "text": text,
                "words": words,
            })
    return {"language": info.language, "segments": segments}


def transcribe(src: str, work_dir: Path, model_name: str = "auto",
               language: str = "auto", vocabulary: list | None = None) -> dict:
    """Возвращает {'language': ..., 'segments': [{start, end, text, words: [...]}]}."""
    wav = work_dir / "audio16k.wav"
    extract_audio(src, wav)
    lang = None if language in ("auto", "", None) else language
    # Подсказка распознаванию: имена и термины, которые оно иначе слышит
    # как попало. Действует на входе, а не заменой в готовом тексте.
    hint = ", ".join(str(v) for v in vocabulary if str(v).strip()) if vocabulary else None
    if hint:
        print(f"  подсказка распознаванию: {hint[:70]}")

    _add_cuda_dll_dirs()
    try:
        result = _run_whisper(str(wav), model_name, "cuda", lang, hint)
        print("  Whisper отработал на GPU (CUDA)")
    except Exception as exc:
        print(f"  CUDA недоступна ({type(exc).__name__}: {str(exc)[:120]}), распознаю на CPU...")
        result = _run_whisper(str(wav), model_name, "cpu", lang, hint)

    (work_dir / "transcript.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    wav.unlink(missing_ok=True)
    return result
