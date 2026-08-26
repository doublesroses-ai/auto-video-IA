"""Текст → готовое вертикальное видео: нейроозвучка + фон + субтитры + музыка."""
import json
import random
import time
from pathlib import Path

from .config import load_config, OUTPUT_DIR, WORK_DIR, PROJECT_DIR, VIDEO_EXTENSIONS
from .ffmpeg_utils import ffmpeg, run, duration_of, filter_path, video_encoder_args
from .render import pick_music
from .subtitles import build_ass
from .tts import synthesize

BACKGROUNDS_DIR = PROJECT_DIR / "backgrounds"


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _pick_background() -> str | None:
    if not BACKGROUNDS_DIR.is_dir():
        return None
    videos = [p for p in BACKGROUNDS_DIR.glob("*") if p.suffix.lower() in VIDEO_EXTENSIONS]
    return str(random.choice(videos)) if videos else None


def text_to_video(text: str, name: str, config: dict | None = None) -> Path:
    """Собирает вертикальное видео из текста. Возвращает папку с результатом."""
    cfg = config or load_config()
    out_dir = OUTPUT_DIR / name
    work = WORK_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    tts_cfg = cfg["tts"]
    vert = cfg["vertical"]
    subs = cfg["subtitles"]
    music_cfg = cfg["music"]
    w, h = vert["width"], vert["height"]

    _log(f"=== Текст → видео: {name} ===")
    _log("Шаг 1/3: озвучиваю текст нейроголосом...")
    voice_mp3 = work / "voice.mp3"
    transcript = synthesize(text, tts_cfg["voice"], tts_cfg["rate"], voice_mp3)
    dur = duration_of(str(voice_mp3)) + 0.6
    n_words = sum(len(s["words"]) for s in transcript["segments"])
    _log(f"  озвучено {n_words} слов, длительность {dur:.0f} с")

    _log("Шаг 2/3: собираю субтитры...")
    ass_text = build_ass(
        transcript, 0.0, dur, w, h,
        subs["font"], subs["vertical_font_size"],
        subs["uppercase"], subs["max_words_per_card"],
        bottom_margin_ratio=0.30,
    )
    ass_file = work / "tts.ass"
    ass_file.write_text(ass_text, encoding="utf-8")

    _log("Шаг 3/3: рендерю видео...")
    background = _pick_background()
    music = pick_music() if music_cfg["enabled"] else None
    sub = filter_path(str(ass_file))

    cmd = [ffmpeg(), "-y"]
    if background:
        cmd += ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", background]
        _log(f"  фон: {Path(background).name}")
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}",
                "-i", f"gradients=size={w}x{h}:speed=0.02:nb_colors=4"]
        _log("  фон: анимированный градиент (можно положить свои видео в backgrounds/)")
    cmd += ["-i", str(voice_mp3)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        _log(f"  музыка: {Path(music).name}")

    vchain = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
              f"crop={w}:{h},format=yuv420p,ass='{sub}'[vout]")
    if music:
        achain = (
            f"[2:a]volume={music_cfg['volume']},aformat=sample_rates=48000[m];"
            "[1:a]aformat=sample_rates=48000,asplit=2[sc][sp];"
            "[m][sc]sidechaincompress=threshold=0.05:ratio=10:attack=10:release=350[duck];"
            "[sp][duck]amix=inputs=2:duration=first:normalize=0,"
            "loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )
    else:
        achain = "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[aout]"

    dst = out_dir / f"{name}.mp4"
    cmd += [
        "-filter_complex", vchain + ";" + achain,
        "-map", "[vout]", "-map", "[aout]",
        *video_encoder_args(cfg["render"]["shorts_max_mbps"]),
        "-c:a", "aac", "-b:a", "192k",
        "-t", f"{dur:.3f}", "-movflags", "+faststart", str(dst),
    ]
    run(cmd, desc="рендер видео из текста")

    (out_dir / "transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "text.txt").write_text(text, encoding="utf-8")

    _log(f"=== Готово → {dst} ===")
    return out_dir


def text_file_to_video(txt_path: str | Path, config: dict | None = None) -> Path:
    txt_path = Path(txt_path)
    raw = txt_path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"Не удалось прочитать текст из {txt_path.name}")
    return text_to_video(text, txt_path.stem, config)
