"""Рендер финальных роликов: вертикальные шортсы 9:16 и версия 16:9."""
import random
from pathlib import Path

from .config import MUSIC_DIR, MUSIC_EXTENSIONS
from .ffmpeg_utils import ffmpeg, run, filter_path, video_encoder_args, has_audio


def pick_music() -> str | None:
    tracks = [p for p in MUSIC_DIR.glob("*") if p.suffix.lower() in MUSIC_EXTENSIONS]
    return str(random.choice(tracks)) if tracks else None


def _audio_chain_from(speech_label: str, music_index: int | None,
                      music_volume: float, fade_at: float | None = None) -> str:
    """Аудиограф: речь + приглушаемая под неё музыка (ducking) + нормализация."""
    fade = f",afade=t=out:st={fade_at:.2f}:d=0.5" if fade_at and fade_at > 1 else ""
    if music_index is not None:
        return (
            f"[{music_index}:a]volume={music_volume},aformat=sample_rates=48000[mus];"
            f"[{speech_label}]aformat=sample_rates=48000,asplit=2[sc][speech];"
            "[mus][sc]sidechaincompress=threshold=0.05:ratio=10:attack=10:release=350[duck];"
            "[speech][duck]amix=inputs=2:duration=first:normalize=0,"
            f"loudnorm=I=-14:TP=-1.5:LRA=11{fade}[aout]"
        )
    return f"[{speech_label}]loudnorm=I=-14:TP=-1.5:LRA=11{fade}[aout]"


def _audio_chain(has_music: bool, music_volume: float, fade_at: float | None = None) -> str:
    """Старая точка входа: речь во входе 0, музыка во входе 1."""
    return _audio_chain_from("0:a", 1 if has_music else None, music_volume, fade_at)


def _vertical_chain(label_in: str, width: int, height: int, background: str,
                    sub: str, vfade: str) -> str:
    """Общая для одного и нескольких кусков сборка вертикального кадра.

    Заголовок рисуется не здесь, а в файле субтитров: там есть перенос строк
    и настоящие стили, а drawtext молча уезжал за края кадра на длинном тексте.
    """
    card = ""
    if background == "crop":
        return (
            f"[{label_in}]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},ass='{sub}'{card}{vfade}[vout]"
        )
    # decrease, а не фиксированная ширина: у вертикального исходника кадр
    # иначе становится выше экрана, и overlay срезает верх и низ
    return (
        f"[{label_in}]split[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},boxblur=22:2[b];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[f];"
        f"[b][f]overlay=(W-w)/2:(H-h)/2,ass='{sub}'{card}{vfade}[vout]"
    )


SEEK_MARGIN = 3.0     # запас перед точкой реза, иначе ffmpeg декодирует файл с нуля
SEAM_FADE = 0.04      # микрофейд на стыке кусков, иначе щёлкает


def render_vertical(src: str, pieces: list[list[float]], ass_file: str, dst: str,
                    width: int, height: int, background: str,
                    music: str | None, music_volume: float,
                    max_mbps: float | None = 8, title: str = "",
                    font: str = "Arial Black", work_dir=None) -> None:
    """Собирает вертикальный шортс из одного или нескольких кусков исходника.

    Куски склеиваются встык (jump cut — для шортса это нормально), звук на стыках
    получает микрофейды. Субтитры и заголовок накладываются уже на склеенное.
    """
    pieces = [[float(a), float(b)] for a, b in pieces if b > a]
    if not pieces:
        raise ValueError("Нечего рендерить: список кусков пуст")

    total = sum(b - a for a, b in pieces)
    sub = filter_path(ass_file)
    vfade = f",fade=t=out:st={max(total - 0.45, 0):.2f}:d=0.45" if total > 3 else ""

    cmd = [ffmpeg(), "-y"]
    parts = []
    labels = []
    for k, (a, b) in enumerate(pieces):
        seek = max(a - SEEK_MARGIN, 0.0)
        inner = a - seek                      # где кусок начинается внутри входа
        dur = b - a
        cmd += ["-ss", f"{seek:.3f}", "-t", f"{dur + inner + 0.5:.3f}", "-i", str(src)]
        parts.append(
            f"[{k}:v]trim=start={inner:.3f}:end={inner + dur:.3f},setpts=PTS-STARTPTS[v{k}];"
            f"[{k}:a]atrim=start={inner:.3f}:end={inner + dur:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={SEAM_FADE},"
            f"afade=t=out:st={max(dur - SEAM_FADE, 0):.3f}:d={SEAM_FADE}[a{k}];"
        )
        labels.append(f"[v{k}][a{k}]")

    n = len(pieces)
    if n > 1:
        parts.append(f"{''.join(labels)}concat=n={n}:v=1:a=1[cv][ca];")
        vin, ain = "cv", "ca"
    else:
        vin, ain = "v0", "a0"

    music_index = n if music else None
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]

    achain = _audio_chain_from(ain, music_index, music_volume, fade_at=total - 0.55)
    script = ("".join(parts)
              + _vertical_chain(vin, width, height, background, sub, vfade)
              + ";" + achain)

    # длинный граф передаём файлом: в командной строке Windows он не помещается
    if work_dir is not None:
        script_file = Path(work_dir) / f"render_{Path(dst).stem}.txt"
        script_file.write_text(script, encoding="utf-8")
        cmd += ["-/filter_complex", str(script_file)]
    else:
        cmd += ["-filter_complex", script]

    cmd += [
        "-map", "[vout]", "-map", "[aout]",
        *video_encoder_args(max_mbps),
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{total:.3f}", "-movflags", "+faststart", str(dst),
    ]
    run(cmd, desc="рендер шортса")


def render_horizontal(src: str, ass_file: str, dst: str,
                      max_mbps: float | None = 12) -> None:
    """Полная 16:9 версия с вшитыми субтитрами и нормализацией звука."""
    sub = filter_path(ass_file)
    audio = ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"] if has_audio(src) else []
    run(
        [ffmpeg(), "-y", "-i", str(src),
         "-vf", f"ass='{sub}'",
         *audio,
         *video_encoder_args(max_mbps),
         "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(dst)],
        desc="рендер 16:9",
    )
