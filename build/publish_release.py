"""Публикация релиза на GitHub с прикреплённым установщиком.

Учётные данные берутся у git (тот же вход, которым работает push) и никуда
не выводятся — в консоль попадают только адреса и статусы.

Запуск:  .venv\\Scripts\\python.exe build\\publish_release.py [версия]
"""
import json
import mimetypes
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "doublesroses-ai/auto-video-IA"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"


def log(msg: str) -> None:
    print(msg, flush=True)


def get_token() -> str:
    """Берёт токен у git-помощника учётных данных. Значение не печатаем."""
    proc = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, cwd=str(ROOT),
    )
    if proc.returncode != 0:
        raise SystemExit("git не смог выдать учётные данные для github.com")
    for line in proc.stdout.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    raise SystemExit("В учётных данных git нет токена для github.com")


def api(token: str, method: str, url: str, payload=None,
        data: bytes = None, content_type: str = None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "AutoVideoIA-release",
    }
    body = data
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=600) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


NOTES = """Первая версия приложения для Windows.

**Установка:** скачать `AutoVideoIA-Setup-1.0.0.exe`, запустить, при окне
«Windows защитила ваш компьютер» нажать «Подробнее» → «Выполнить в любом случае».
Python, ffmpeg и git ставить не нужно — всё внутри. Права администратора не требуются.

После установки откроется окно первой настройки: оно докачает модель распознавания
речи и, по желанию, нейросеть для заголовков. Нужен интернет; любой шаг можно пропустить.

**Что умеет**

- Длинное видео (стрим, подкаст, летсплей) → вертикальные шортсы 9:16 с субтитрами
  в стиле караоке, вырезанными паузами и фоновой музыкой.
- Полная версия 16:9 с вшитыми субтитрами для YouTube.
- Текстовый файл → видео с озвучкой нейроголосом.
- Правка ошибок распознавания с пересборкой роликов.
- Автоматический режим: файлы из папки `input` обрабатываются сами.

**Где что лежит**

| Что | Где |
|---|---|
| Программа | `%LOCALAPPDATA%\\Programs\\AutoVideoIA` |
| Видео (`input`, `output`, `music`, `backgrounds`) | `Видео\\AutoVideoIA` |
| Служебное (`work`, `logs`) | `%LOCALAPPDATA%\\AutoVideoIA` |

Видео лежат отдельно от программы и переживают её переустановку и удаление.

**Требования:** Windows 10/11 64-бит. Видеокарта NVIDIA ускоряет распознавание речи
в десятки раз, но не обязательна — без неё всё считается на процессоре, просто дольше.
"""


def main() -> int:
    version = sys.argv[1] if len(sys.argv) > 1 else "1.0.0"
    tag = f"v{version}"
    asset = ROOT / "installer" / "output" / f"AutoVideoIA-Setup-{version}.exe"
    if not asset.exists():
        raise SystemExit(f"Установщик не найден: {asset}")

    token = get_token()
    log(f"Репозиторий: {REPO}")
    info = api(token, "GET", f"{API}/repos/{REPO}")
    log(f"Доступ есть. Репозиторий {'закрытый' if info['private'] else 'публичный'}.")

    # уже существующий релиз с таким тегом заменяем, чтобы не плодить дубли
    try:
        old = api(token, "GET", f"{API}/repos/{REPO}/releases/tags/{tag}")
        log(f"Релиз {tag} уже существует — удаляю старый")
        api(token, "DELETE", f"{API}/repos/{REPO}/releases/{old['id']}")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise

    log(f"Создаю релиз {tag}...")
    release = api(token, "POST", f"{API}/repos/{REPO}/releases", payload={
        "tag_name": tag,
        "target_commitish": "main",
        "name": f"Автомонтаж видео {version}",
        "body": NOTES,
        "draft": False,
        "prerelease": False,
    })

    size_mb = asset.stat().st_size / 1024 / 1024
    log(f"Загружаю {asset.name} ({size_mb:.0f} МБ)...")
    upload_url = f"{UPLOADS}/repos/{REPO}/releases/{release['id']}/assets?name={asset.name}"
    ctype = mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
    uploaded = api(token, "POST", upload_url, data=asset.read_bytes(), content_type=ctype)

    log("")
    log("=== ГОТОВО ===")
    log(f"Страница релиза: {release['html_url']}")
    log(f"Прямая ссылка:   {uploaded['browser_download_url']}")
    if info["private"]:
        log("")
        log("Репозиторий закрытый: ссылка откроется только после входа")
        log("в твой аккаунт GitHub на том компьютере.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
