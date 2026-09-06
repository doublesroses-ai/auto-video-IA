"""Диктор: озвучивает реплики нейроголосом и подмешивает их в готовый ролик.

Реплики берутся с экрана (screen_text) или пишутся руками. Каждая озвучивается
отдельно и ставится на своё время; речь стримера и музыка под диктором
приглушаются, чтобы два голоса не спорили. Если последняя реплика не влезает
в ролик, он продлевается стоп-кадром — но не больше, чем на несколько секунд.
"""
import asyncio
import json
from pathlib import Path

from .ffmpeg_utils import ffmpeg, run, duration_of, video_encoder_args
from .tts import _synth_async

GAP_SEC = 0.25        # пауза между репликами, если они наезжают друг на друга
MAX_EXTEND_SEC = 6.0  # насколько можно продлить ролик ради последней реплики
VOLUME = 1.0


def synthesize_lines(lines: list[dict], voice: str, rate: str,
                     out_dir: Path) -> list[dict]:
    """Озвучивает реплики по одной. Возвращает копии с полями file и dur.

    Нужен интернет: голоса живут у Microsoft. При ошибке сети — исключение.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def cached(mp3: Path, stamp: dict) -> bool:
        # Реплика уже озвучена этим голосом — не ходим в сеть второй раз:
        # пересбор субтитров или заголовка тогда работает и без интернета.
        tag = mp3.with_suffix(".json")
        try:
            return (mp3.stat().st_size > 100
                    and json.loads(tag.read_text(encoding="utf-8")) == stamp)
        except (OSError, ValueError):
            return False

    async def synth_all():
        result = []
        for n, line in enumerate(lines):
            text = " ".join(str(line["text"]).split())
            if not text:
                continue
            mp3 = out_dir / f"narr_{n:02d}.mp3"
            stamp = {"voice": voice, "rate": rate, "text": text}
            if not cached(mp3, stamp):
                await _synth_async(text, voice, rate, str(mp3))
                if not mp3.exists() or mp3.stat().st_size < 100:
                    raise RuntimeError(f"Озвучка не удалась: «{text[:40]}» "
                                       "(проверь интернет-соединение)")
                mp3.with_suffix(".json").write_text(
                    json.dumps(stamp, ensure_ascii=False), encoding="utf-8")
            result.append({**line, "text": text, "file": str(mp3),
                           "dur": duration_of(str(mp3))})
        return result

    return asyncio.run(synth_all())


def schedule(lines: list[dict]) -> list[dict]:
    """Расставляет реплики по времени так, чтобы они не накладывались."""
    out = []
    cursor = 0.0
    for line in sorted(lines, key=lambda l: float(l["start"])):
        start = max(float(line["start"]), cursor)
        out.append({**line, "start": round(start, 3)})
        cursor = start + float(line["dur"]) + GAP_SEC
    return out


def _mix_chain(n: int, delays_ms: list[int], duck: bool, extend: float) -> str:
    parts = []
    for k, d in enumerate(delays_ms, start=1):
        parts.append(f"[{k}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"volume={VOLUME},adelay={d}|{d}[n{k}];")
    inputs = "".join(f"[n{k}]" for k in range(1, n + 1))
    if n == 1:
        narr = "[n1]"
    else:
        parts.append(f"{inputs}amix=inputs={n}:duration=longest:normalize=0[narr];")
        narr = "[narr]"
    parts.append("[0:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                 f"apad=pad_dur={extend:.3f}[orig];")
    if duck:
        # компрессор с боковой цепью: пока звучит диктор, остальное тише на ~20 дБ
        parts.append(f"{narr}asplit=2[nm][nsc];"
                     "[orig][nsc]sidechaincompress=threshold=0.02:ratio=8:"
                     "attack=30:release=600[dk];")
        base, voice = "[dk]", "[nm]"
    else:
        base, voice = "[orig]", narr
    parts.append(f"{base}{voice}amix=inputs=2:duration=longest:normalize=0,"
                 "alimiter=limit=0.95:level=disabled[aout]")
    return "".join(parts)


def mix_into(video: str | Path, lines: list[dict], dst: str | Path,
             duck: bool = True, max_mbps: float | None = 8) -> float:
    """Подмешивает озвученные реплики (после synthesize_lines и schedule)
    в готовый ролик. Возвращает длительность результата."""
    lines = [l for l in lines if l.get("file")]
    if not lines:
        raise ValueError("Нет ни одной озвученной реплики")
    video_dur = duration_of(str(video))
    last_end = max(float(l["start"]) + float(l["dur"]) for l in lines)
    extend = min(max(last_end + 0.4 - video_dur, 0.0), MAX_EXTEND_SEC)
    total = video_dur + extend

    cmd = [ffmpeg(), "-y", "-i", str(video)]
    delays = []
    for l in lines:
        cmd += ["-i", l["file"]]
        delays.append(int(round(float(l["start"]) * 1000)))
    chain = _mix_chain(len(lines), delays, duck, extend)

    if extend > 0.05:
        # реплика длиннее ролика — держим последний кадр, пока она не закончится
        chain = f"[0:v]tpad=stop_mode=clone:stop_duration={extend:.3f}[vout];" + chain
        cmd += ["-filter_complex", chain, "-map", "[vout]", "-map", "[aout]",
                *video_encoder_args(max_mbps)]
    else:
        cmd += ["-filter_complex", chain, "-map", "0:v", "-map", "[aout]",
                "-c:v", "copy"]
    cmd += ["-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}",
            "-movflags", "+faststart", str(dst)]
    run(cmd, desc="подмешивание диктора")
    return total
