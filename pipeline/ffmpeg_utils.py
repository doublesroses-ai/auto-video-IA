"""Поиск и запуск ffmpeg/ffprobe."""
import functools
import glob
import json
import os
import shutil
import subprocess


def _winget_links_candidate(name: str) -> str | None:
    links = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", f"{name}.exe")
    if os.path.isfile(links):
        return links
    packages = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    for hit in glob.glob(os.path.join(packages, "Gyan.FFmpeg*", "**", "bin", f"{name}.exe"), recursive=True):
        return hit
    return None


@functools.cache
def find_tool(name: str) -> str:
    path = shutil.which(name) or _winget_links_candidate(name)
    if not path:
        raise RuntimeError(
            f"{name} не найден. Установи его командой: winget install Gyan.FFmpeg"
        )
    return path


def ffmpeg() -> str:
    return find_tool("ffmpeg")


def ffprobe() -> str:
    return find_tool("ffprobe")


def run(cmd: list[str], desc: str = "") -> subprocess.CompletedProcess:
    """Запускает команду, при ошибке показывает хвост stderr."""
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-2500:]
        raise RuntimeError(f"Команда не выполнилась ({desc or cmd[0]}):\n{tail}")
    return proc


def probe(path: str) -> dict:
    proc = run(
        [ffprobe(), "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        desc="ffprobe",
    )
    return json.loads(proc.stdout)


def duration_of(path: str) -> float:
    info = probe(path)
    return float(info["format"]["duration"])


def video_size(path: str) -> tuple[int, int]:
    info = probe(path)
    for stream in info["streams"]:
        if stream.get("codec_type") == "video":
            return int(stream["width"]), int(stream["height"])
    raise RuntimeError(f"В файле нет видеопотока: {path}")


def has_audio(path: str) -> bool:
    info = probe(path)
    return any(s.get("codec_type") == "audio" for s in info["streams"])


@functools.cache
def _has_nvenc() -> bool:
    try:
        subprocess.run(
            [ffmpeg(), "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, check=True, timeout=30,
        )
        return True
    except Exception:
        return False


def video_encoder_args(max_mbps: float | None = None) -> list[str]:
    """NVENC, если видеокарта NVIDIA доступна, иначе libx264.

    max_mbps ограничивает пиковый битрейт. Без него кодировщик в режиме
    постоянного качества раздувает вертикальные ролики до 15+ Мбит/с
    (90 МБ за 45 секунд) — платформы всё равно пережимают их вдвое слабее.
    """
    if _has_nvenc():
        args = ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
                "-rc", "vbr", "-cq", "23", "-b:v", "0"]
    else:
        args = ["-c:v", "libx264", "-preset", "fast", "-crf", "22"]
    if max_mbps:
        args += ["-maxrate", f"{max_mbps:g}M", "-bufsize", f"{max_mbps * 2:g}M"]
    return args


def filter_path(path: str) -> str:
    """Экранирует путь Windows для использования внутри фильтров ffmpeg (ass=...)."""
    p = str(path).replace("\\", "/")
    p = p.replace(":", "\\:")
    p = p.replace("'", "\\'")
    return p
