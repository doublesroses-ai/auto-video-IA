"""Обнаружение тишины и монтаж «jump cut»: вырезаем паузы из видео."""
import re
import subprocess
from pathlib import Path

from .ffmpeg_utils import ffmpeg, run, duration_of, video_encoder_args

_SILENCE_START = re.compile(r"silence_start:\s*([\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([\d.]+)")


def detect_silences(src: str, noise_db: float, min_silence: float) -> list[tuple[float, float]]:
    """Возвращает список интервалов тишины (start, end)."""
    proc = subprocess.run(
        [ffmpeg(), "-i", str(src),
         "-af", f"silencedetect=noise={noise_db}dB:d={min_silence}",
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    log = proc.stderr or ""
    starts = [float(m) for m in _SILENCE_START.findall(log)]
    ends = [float(m) for m in _SILENCE_END.findall(log)]
    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is None:
            e = duration_of(src)
        silences.append((s, e))
    return silences


def keep_intervals(total: float, silences: list[tuple[float, float]],
                   pad: float) -> list[tuple[float, float]]:
    """Инвертирует тишину в интервалы речи с запасом pad с каждой стороны."""
    keeps = []
    cursor = 0.0
    for s, e in silences:
        seg_end = min(s + pad, total)
        if seg_end - cursor > 0.15:
            keeps.append((cursor, seg_end))
        cursor = max(e - pad, cursor)
    if total - cursor > 0.15:
        keeps.append((cursor, total))

    # склеиваем интервалы, между которыми почти нет разрыва
    merged: list[list[float]] = []
    for s, e in keeps:
        if merged and s - merged[-1][1] < 0.25:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s > 0.2]


def cut_silences(src: str, dst: str, work_dir: Path,
                 noise_db: float, min_silence: float, pad: float) -> dict:
    """Вырезает паузы. Возвращает статистику. Если резать нечего — просто копирует."""
    total = duration_of(src)
    silences = detect_silences(src, noise_db, min_silence)
    keeps = keep_intervals(total, silences, pad)

    kept = sum(e - s for s, e in keeps)
    stats = {"original_sec": total, "kept_sec": kept or total, "segments": len(keeps)}

    # если вырезается меньше 3% — не тратим время на перекодирование
    if not keeps or kept > total * 0.97:
        run([ffmpeg(), "-y", "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)],
            desc="копирование без обрезки")
        stats["kept_sec"] = total
        return stats

    parts_v, parts_a, labels = [], [], []
    for i, (s, e) in enumerate(keeps):
        parts_v.append(f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}];")
        parts_a.append(f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}];")
        labels.append(f"[v{i}][a{i}]")
    script = "".join(parts_v) + "".join(parts_a) + \
        f"{''.join(labels)}concat=n={len(keeps)}:v=1:a=1[vout][aout]"

    script_file = work_dir / "cut_filter.txt"
    script_file.write_text(script, encoding="utf-8")

    run(
        [ffmpeg(), "-y", "-i", str(src),
         "-/filter_complex", str(script_file),
         "-map", "[vout]", "-map", "[aout]",
         *video_encoder_args(),
         "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", str(dst)],
        desc="вырезание пауз",
    )
    return stats
