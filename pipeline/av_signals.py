"""Сигналы из самой картинки и звука: где экран застыл, где темно, где эмоции.

Отбор моментов читает только расшифровку и потому не знает, что в это время
на экране открыто меню или карта космоса. Здесь мы дёшево измеряем видео
двумя проходами ffmpeg и даём отбору три числа на любой отрезок:
  * доля застывшего экрана,
  * доля тёмных кадров,
  * громкость голоса относительно обычной.

Все пороги считаются от самого видео: у стрима и у записи с телефона
и яркость, и громкость отличаются в разы, абсолютные числа тут промахиваются.
"""
import re
import statistics
import subprocess
from pathlib import Path

from .ffmpeg_utils import ffmpeg

_FRAME_TIME = re.compile(r"pts_time:([\d.]+)")
_KEY_VALUE = re.compile(r"lavfi\.([\w.]+)=(-?[\d.]+)")

# ниже этого уровня замер громкости считается тишиной, а не речью
_SILENCE_LUFS = -70.0


def _run_capture(cmd: list[str], timeout: float) -> str:
    """Запускает ffmpeg и возвращает его вывод. При заминке — пустую строку."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        return proc.stderr or "" if proc.returncode == 0 else ""
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _parse_metadata(text: str) -> list[tuple[float, str, float]]:
    """Разбирает вывод фильтра metadata: [(время, ключ, значение)]."""
    out = []
    now = 0.0
    for line in text.splitlines():
        m = _FRAME_TIME.search(line)
        if m:
            now = float(m.group(1))
            continue
        m = _KEY_VALUE.search(line)
        if m:
            out.append((now, m.group(1), float(m.group(2))))
    return out


def _freeze_zones(rows: list[tuple[float, str, float]], total: float) -> list[tuple[float, float]]:
    """Интервалы, где картинка стояла неподвижно."""
    zones = []
    start = None
    for _, key, value in rows:
        if key.endswith("freeze_start"):
            start = value
        elif key.endswith("freeze_end") and start is not None:
            zones.append((start, value))
            start = None
    if start is not None:
        zones.append((start, total))
    return zones


def _overlap(a: float, b: float, zones: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(b, z2) - max(a, z1)) for z1, z2 in zones)


class Signals:
    """Измерения по видео. Пустой объект (nothing()) ничего не меняет в отборе."""

    def __init__(self, duration: float = 0.0):
        self.duration = duration
        self.freeze_zones: list[tuple[float, float]] = []
        self.brightness: list[tuple[float, float]] = []   # (время, YAVG)
        self.loudness: list[tuple[float, float]] = []     # (время, LUFS-M)
        self.dark_level = 0.0
        self.loud_base = 0.0
        self.static_video = False   # видео статично по своей природе
        self.ok = False

    @classmethod
    def nothing(cls) -> "Signals":
        return cls()

    # ---------- измерения по отрезку ----------

    def freeze_share(self, a: float, b: float) -> float:
        if not self.ok or b <= a or self.static_video:
            return 0.0
        return _overlap(a, b, self.freeze_zones) / (b - a)

    def dark_share(self, a: float, b: float) -> float:
        if not self.ok or not self.brightness:
            return 0.0
        inside = [v for t, v in self.brightness if a <= t <= b]
        if not inside:
            return 0.0
        return sum(1 for v in inside if v < self.dark_level) / len(inside)

    def energy(self, a: float, b: float) -> float:
        """Насколько громче обычного говорят на отрезке, в децибелах.

        Берём среднее по самым громким 10% замеров: среднее по всему отрезку
        сглаживает как раз то, ради чего мы и меряем.
        """
        if not self.ok or not self.loudness:
            return 0.0
        inside = [v for t, v in self.loudness if a <= t <= b and v > _SILENCE_LUFS]
        if not inside:
            return 0.0
        inside.sort(reverse=True)
        top = inside[:max(1, len(inside) // 10)]
        return sum(top) / len(top) - self.loud_base


def measure(video: str | Path, work_dir: Path, duration: float) -> Signals:
    """Меряет видео. При любой заминке возвращает пустой объект — отбор не пострадает.

    Вывод фильтров забираем прямо из ffmpeg, а не через файл: путь к файлу
    приходится вставлять внутрь строки фильтра, и двоеточие диска её ломает.
    """
    sig = Signals(duration)

    # Картинка: уменьшаем до 256x144 и 5 кадров в секунду — этого хватает,
    # чтобы поймать застывший экран и темноту, а считается в десятки раз быстрее.
    video_out = _run_capture([
        ffmpeg(), "-hide_banner", "-nostats", "-v", "info", "-an", "-i", str(video),
        "-vf", "scale=256:144,fps=5,freezedetect=n=-48dB:d=1.5,signalstats,"
               "metadata=mode=print",
        "-f", "null", "-",
    ], timeout=1800)

    # Звук: видео не декодируется вообще, поэтому считается за пару секунд.
    audio_out = _run_capture([
        ffmpeg(), "-hide_banner", "-nostats", "-v", "info", "-vn", "-i", str(video),
        "-af", "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M",
        "-f", "null", "-",
    ], timeout=900)

    if video_out:
        rows = _parse_metadata(video_out)
        sig.freeze_zones = _freeze_zones(rows, duration)
        sig.brightness = [(t, v) for t, k, v in rows if k.endswith("YAVG")]
        if sig.brightness:
            sig.dark_level = statistics.median(v for _, v in sig.brightness) * 0.55
        frozen = sum(b - a for a, b in sig.freeze_zones)
        sig.static_video = duration > 0 and frozen / duration >= 0.25

    if audio_out:
        rows = _parse_metadata(audio_out)
        vals = [(t, v) for t, k, v in rows if k.endswith("r128.M") and v > _SILENCE_LUFS]
        sig.loudness = vals
        if vals:
            sig.loud_base = statistics.median(v for _, v in vals)

    sig.ok = bool(sig.freeze_zones or sig.brightness or sig.loudness)
    return sig
