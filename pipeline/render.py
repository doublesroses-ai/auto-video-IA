"""Рендер финальных роликов: вертикальные шортсы 9:16 и версия 16:9."""
import random
from pathlib import Path

from .config import MUSIC_DIR, MUSIC_EXTENSIONS
from .ffmpeg_utils import ffmpeg, run, filter_path, video_encoder_args, has_audio


def pick_music() -> str | None:
    tracks = [p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in MUSIC_EXTENSIONS]
    return str(random.choice(tracks)) if tracks else None


def _audio_chain(has_music: bool, music_volume: float, fade_at: float | None = None) -> str:
    """Собирает аудиограф: речь + приглушаемая музыка (ducking) + нормализация."""
    fade = f",afade=t=out:st={fade_at:.2f}:d=0.5" if fade_at and fade_at > 1 else ""
    if has_music:
        return (
            f"[1:a]volume={music_volume},aformat=sample_rates=48000[mus];"
            "[0:a]aformat=sample_rates=48000,asplit=2[sc][speech];"
            "[mus][sc]sidechaincompress=threshold=0.05:ratio=10:attack=10:release=350[duck];"
            "[speech][duck]amix=inputs=2:duration=first:normalize=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11{fade}[aout]"
        )
    return f"[0:a]loudnorm=I=-14:TP=-1.5:LRA=11{fade}[aout]"


def render_vertical(src: str, start: float, end: float, ass_file: str, dst: str,
                    width: int, height: int, background: str,
                    music: str | None, music_volume: float) -> None:
    """Вырезает клип и рендерит вертикальный шортс с субтитрами и музыкой."""
    dur = end - start
    sub = filter_path(ass_file)
    vfade = f",fade=t=out:st={max(dur - 0.45, 0):.2f}:d=0.45" if dur > 3 else ""

    if background == "crop":
        vchain = (
            f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},ass='{sub}'{vfade}[vout]"
        )
    else:  # blur: размытый фон + оригинал по центру
        vchain = (
            f"[0:v]split[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},boxblur=22:2[b];"
            f"[fg]scale={width}:-2[f];"
            f"[b][f]overlay=(W-w)/2:(H-h)/2,ass='{sub}'{vfade}[vout]"
        )

    cmd = [ffmpeg(), "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(src)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
    cmd += [
        "-filter_complex",
        vchain + ";" + _audio_chain(bool(music), music_volume, fade_at=dur - 0.55),
        "-map", "[vout]", "-map", "[aout]",
        *video_encoder_args(),
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(dst),
    ]
    run(cmd, desc="рендер шортса")


def render_horizontal(src: str, ass_file: str, dst: str) -> None:
    """Полная 16:9 версия с вшитыми субтитрами и нормализацией звука."""
    sub = filter_path(ass_file)
    audio = ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"] if has_audio(src) else []
    run(
        [ffmpeg(), "-y", "-i", str(src),
         "-vf", f"ass='{sub}'",
         *audio,
         *video_encoder_args(),
         "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(dst)],
        desc="рендер 16:9",
    )
