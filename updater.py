"""Проверка и установка обновлений из GitHub Releases.

Осторожность здесь не лишняя: код скачивает и запускает исполняемый файл.
Поэтому проверяем всё, что можно проверить:
  * адрес скачивания обязан вести на домен GitHub;
  * контрольная сумма сверяется с той, что заявлена в релизе;
  * версии сравниваются по числам, а не строками (иначе 1.10 < 1.9);
  * файл скачивается во временное имя и переименовывается только целиком.
Пользователя всегда спрашивают перед установкой — молча ничего не ставится.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "1.0.0"
REPO = "doublesroses-ai/auto-video-IA"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ALLOWED_HOSTS = ("github.com", "api.github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")
USER_AGENT = f"AutoVideoIA/{VERSION}"


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.10.2' -> (1, 10, 2). Некорректное превращается в (0,)."""
    nums = re.findall(r"\d+", text or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def _host_ok(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def check() -> dict | None:
    """Возвращает сведения о новой версии либо None."""
    req = urllib.request.Request(API_URL, headers={
        "User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None  # нет интернета или лимит запросов — молча живём дальше

    tag = data.get("tag_name", "")
    if parse_version(tag) <= parse_version(VERSION):
        return None
    for asset in data.get("assets", []):
        name = asset.get("name", "")
        url = asset.get("browser_download_url", "")
        if name.lower().endswith(".exe") and _host_ok(url):
            digest = (asset.get("digest") or "").removeprefix("sha256:")
            return {"version": tag, "name": name, "url": url,
                    "sha256": digest, "size": asset.get("size", 0),
                    "notes": (data.get("body") or "")[:1500]}
    return None


def download(info: dict, progress=None) -> Path:
    """Качает установщик во временную папку, сверяет сумму. Возвращает путь."""
    if not _host_ok(info["url"]):
        raise RuntimeError("Адрес загрузки не принадлежит GitHub — обновление отменено")

    tmp_dir = Path(tempfile.gettempdir()) / "AutoVideoIA-update"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    partial = tmp_dir / (info["name"] + ".part")
    target = tmp_dir / info["name"]

    req = urllib.request.Request(info["url"], headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(partial, "wb") as f:
        total = int(resp.headers.get("Content-Length") or info.get("size") or 0)
        while chunk := resp.read(1 << 20):
            f.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done, total)

    got = digest.hexdigest()
    want = (info.get("sha256") or "").lower()
    if want and got != want:
        partial.unlink(missing_ok=True)
        raise RuntimeError("Контрольная сумма не совпала — файл повреждён "
                           "или подменён, обновление отменено")
    partial.replace(target)  # переименование целиком: недокачанное не запустится
    return target


def install(installer: Path) -> None:
    """Запускает установщик тихо и закрывает приложение."""
    subprocess.Popen([str(installer), "/SILENT", "/CLOSEAPPLICATIONS",
                      "/RESTARTAPPLICATIONS", "/NORESTART"])
    sys.exit(0)


def describe(info: dict) -> str:
    size = info.get("size", 0) / 1024 / 1024
    text = f"Доступна версия {info['version']} (сейчас {VERSION}), {size:.0f} МБ."
    if info.get("notes"):
        text += "\n\nЧто нового:\n" + info["notes"]
    if not info.get("sha256"):
        text += "\n\nВнимание: у релиза нет контрольной суммы, проверить целостность нечем."
    return text


if __name__ == "__main__":
    found = check()
    print(describe(found) if found else f"Установлена последняя версия ({VERSION})")
