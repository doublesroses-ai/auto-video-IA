"""Один шортс от кусков исходника до файла в output: рендер, диктор, правки.

Порядок сборки всегда один: чистый рендер (куски + субтитры + титр)
→ диктор (реплики подмешиваются в звук) → правки (вырезать, картинка,
заставка). Диктор и правки записаны в metadata.json у каждого ролика,
поэтому переживают пересбор субтитров и заголовков: любой пересбор
делает чистый рендер заново и снова накладывает всё записанное.

Чистый рендер хранится в work/<проект>/short_XX.clean.mp4, пока у ролика
есть диктор или правки; без них файл в output и есть чистый рендер.
"""
import json
import shutil
import time
from pathlib import Path

from .config import load_config, OUTPUT_DIR, WORK_DIR, INPUT_DIR
from .subtitles import build_ass_pieces
from .render import render_vertical, pick_music
from .silence import cut_silences
from . import narrator
from . import edits as edits_mod


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _clip_pieces(clip: dict) -> list[list[float]]:
    """Куски клипа. Старые записи без 'pieces' читаются как один кусок."""
    pieces = clip.get("pieces")
    if pieces:
        return [[float(a), float(b)] for a, b in pieces]
    return [[float(clip["start"]), float(clip["end"])]]


def _clip_duration(clip: dict) -> float:
    return sum(b - a for a, b in _clip_pieces(clip))


def _empty_ass(play_w: int, play_h: int) -> str:
    """Пустой файл субтитров: нужен, когда вшивание выключено, чтобы не менять граф."""
    return (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {play_w}\n"
            f"PlayResY: {play_h}\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


def has_extras(clip: dict) -> bool:
    narr = clip.get("narration") or {}
    return bool(narr.get("lines")) or bool(clip.get("edits"))


# ---------------------------------------------------------------- рендер

def render_clean(tight: str | Path, transcript: dict, clip: dict, index: int,
                 cfg: dict, work: Path, dst: Path) -> None:
    """Чистый рендер ролика: куски исходника, субтитры, титр, музыка."""
    subs = cfg["subtitles"]
    vert = cfg["vertical"]
    music_cfg = cfg["music"]
    pieces = _clip_pieces(clip)
    title = clip.get("hook", "") if subs["show_title"] else ""
    ass_text = build_ass_pieces(
        transcript, pieces,
        vert["width"], vert["height"],
        subs["font"], subs["vertical_font_size"],
        subs["uppercase"], subs["max_words_per_card"],
        bottom_margin_ratio=0.30, title=title,
    ) if subs["burn_in"] else _empty_ass(vert["width"], vert["height"])
    ass_file = work / f"short_{index:02d}.ass"
    ass_file.write_text(ass_text, encoding="utf-8")
    music = pick_music() if music_cfg["enabled"] else None
    render_vertical(str(tight), pieces, str(ass_file), str(dst),
                    vert["width"], vert["height"], vert["background"],
                    music, music_cfg["volume"], cfg["render"]["shorts_max_mbps"],
                    title=title, font=subs["font"], work_dir=work)


def clean_path(work: Path, index: int) -> Path:
    return Path(work) / f"short_{index:02d}.clean.mp4"


def finalize(clip: dict, index: int, cfg: dict, work: Path,
             clean: Path, final: Path) -> None:
    """Чистый рендер → + диктор → + правки → готовый файл."""
    work = Path(work)
    cur = Path(clean)
    narr = clip.get("narration") or {}
    lines = narr.get("lines") or []
    if lines:
        _log(f"  диктор: {len(lines)} реплик...")
        synth = narrator.synthesize_lines(
            lines, narr.get("voice") or cfg["tts"]["voice"], cfg["tts"]["rate"],
            work / f"narr_{index:02d}")
        tmp = work / f"short_{index:02d}.narr.mp4"
        narrator.mix_into(cur, narrator.schedule(synth), tmp,
                          duck=narr.get("duck", True),
                          max_mbps=cfg["render"]["shorts_max_mbps"])
        cur = tmp
    ops = clip.get("edits") or []
    if ops:
        _log(f"  правки: {len(ops)}...")
        tmp = work / f"short_{index:02d}.edit.mp4"
        edits_mod.apply_edits(cur, tmp, ops, work / f"edit_{index:02d}",
                              cfg["render"]["shorts_max_mbps"])
        cur = tmp
    final = Path(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    if cur == Path(clean):
        shutil.copy2(cur, final)
    else:
        shutil.move(str(cur), str(final))
        for leftover in (work / f"short_{index:02d}.narr.mp4",):
            leftover.unlink(missing_ok=True)


def render_short(tight: str | Path, transcript: dict, clip: dict, index: int,
                 cfg: dict, work: Path, out_dir: Path) -> Path:
    """Собирает ролик целиком: рендер и, если записаны, диктор с правками."""
    work = Path(work)
    final = Path(out_dir) / clip.get("file", f"shorts/short_{index:02d}.mp4")
    final.parent.mkdir(parents=True, exist_ok=True)
    if not has_extras(clip):
        clean_path(work, index).unlink(missing_ok=True)
        render_clean(tight, transcript, clip, index, cfg, work, final)
        return final
    clean = clean_path(work, index)
    render_clean(tight, transcript, clip, index, cfg, work, clean)
    finalize(clip, index, cfg, work, clean, final)
    return final


# ---------------------------------------------------------------- проект

def _ensure_tight(project: str, meta: dict, cfg: dict) -> Path:
    """Возвращает смонтированное видео без пауз, при необходимости пересобирает."""
    work = WORK_DIR / project
    tight = work / "tight.mp4"
    if tight.exists():
        return tight
    source = INPUT_DIR / "done" / meta["source"]
    if not source.exists():
        raise RuntimeError(
            f"Нет ни рабочего файла work/{project}/tight.mp4, ни исходника "
            f"input/done/{meta['source']} — перерендерить не из чего."
        )
    _log("Рабочий файл не найден, заново вырезаю паузы из исходника...")
    work.mkdir(parents=True, exist_ok=True)
    sil = cfg["silence"]
    if sil["enabled"]:
        cut_silences(str(source), str(tight), work,
                     sil["noise_db"], sil["min_silence_sec"], sil["pad_sec"])
    else:
        shutil.copy2(source, tight)
    return tight


class _Project:
    """Папки и файлы одного проекта из output/."""

    def __init__(self, project: str):
        self.name = project
        self.out_dir = OUTPUT_DIR / project
        self.work = WORK_DIR / project
        self.work.mkdir(parents=True, exist_ok=True)
        self.cfg = load_config()
        self.meta = json.loads((self.out_dir / "metadata.json").read_text(encoding="utf-8"))

    def clip(self, index: int) -> dict:
        shorts = self.meta.get("shorts", [])
        if not 1 <= index <= len(shorts):
            raise ValueError(f"В проекте нет ролика номер {index}")
        return shorts[index - 1]

    def transcript(self) -> dict:
        return json.loads((self.out_dir / "transcript.json").read_text(encoding="utf-8"))

    def save(self) -> None:
        (self.out_dir / "metadata.json").write_text(
            json.dumps(self.meta, ensure_ascii=False, indent=1), encoding="utf-8")

    def final(self, index: int) -> Path:
        return self.out_dir / self.clip(index).get("file", f"shorts/short_{index:02d}.mp4")

    def ensure_clean(self, index: int) -> Path:
        """Чистый рендер ролика: берём готовый, иначе собираем заново."""
        clean = clean_path(self.work, index)
        if clean.exists():
            return clean
        final = self.final(index)
        if final.exists() and not has_extras(self.clip(index)):
            shutil.copy2(final, clean)     # без диктора и правок готовый файл и есть чистый
            return clean
        _log("Чистого рендера нет, собираю ролик заново...")
        tight = _ensure_tight(self.name, self.meta, self.cfg)
        render_clean(tight, self.transcript(), self.clip(index), index,
                     self.cfg, self.work, clean)
        return clean

    def rebuild(self, index: int) -> Path:
        """Накладывает записанные диктора и правки на чистый рендер."""
        clip = self.clip(index)
        final = self.final(index)
        if has_extras(clip):
            clean = self.ensure_clean(index)
            finalize(clip, index, self.cfg, self.work, clean, final)
        else:
            clean = clean_path(self.work, index)
            if clean.exists():
                shutil.move(str(clean), str(final))
        self.save()
        return final


def list_shorts(project: str) -> list[dict]:
    """Ролики проекта для выпадающего списка: номер, заголовок, что наложено."""
    try:
        p = _Project(project)
    except (OSError, ValueError):
        return []
    out = []
    for i, clip in enumerate(p.meta.get("shorts", []), 1):
        narr = clip.get("narration") or {}
        out.append({
            "index": i,
            "file": clip.get("file", f"shorts/short_{i:02d}.mp4"),
            "hook": clip.get("hook", ""),
            "narration": len(narr.get("lines") or []),
            "edits": len(clip.get("edits") or []),
        })
    return out


def current_narration(project: str, index: int) -> dict:
    return dict(_Project(project).clip(index).get("narration") or {})


def find_screen_text(project: str, index: int) -> list[dict]:
    """Читает сообщения с экрана в кусках ролика. Время — от начала ролика."""
    from .screen_text import read_screen_text
    p = _Project(project)
    tight = _ensure_tight(p.name, p.meta, p.cfg)
    return read_screen_text(tight, _clip_pieces(p.clip(index)), p.work)


def set_narration(project: str, index: int, lines: list[dict],
                  voice: str, duck: bool = True) -> Path:
    """Записывает реплики диктора и пересобирает ролик."""
    p = _Project(project)
    clip = p.clip(index)
    lines = [{"start": round(float(l["start"]), 2), "text": " ".join(str(l["text"]).split())}
             for l in lines if str(l.get("text", "")).strip()]
    if lines:
        p.ensure_clean(index)     # до записи: пока диктора нет, готовый файл и есть чистый
        clip["narration"] = {"voice": voice, "duck": bool(duck), "lines": lines}
    else:
        clip.pop("narration", None)
    _log(f"Диктор: {len(lines)} реплик в {clip.get('file')}")
    return p.rebuild(index)


def clear_narration(project: str, index: int) -> Path:
    return set_narration(project, index, [], "")


def _to_clean_time(t: float, ops: list[dict]) -> float:
    """Переводит время готового ролика во время чистого рендера — диктор
    подмешивается до правок, а человек называет секунды по тому, что видит."""
    for op in reversed(ops):
        kind = op.get("op")
        if kind == "cut" and t >= op["from"]:
            t += op["to"] - op["from"]
        elif kind == "trim":
            t += op["start"]
        elif kind == "image":
            at = op.get("at")
            sec = float(op.get("seconds", 3))
            if at == "start":
                t = max(t - sec, 0.0)
            elif at != "end" and t > float(at):
                t = max(t - sec, float(at))
        elif kind == "intro" and op.get("style", "card") == "card":
            t = max(t - edits_mod.CARD_SEC, 0.0)
    return round(t, 2)


def describe_request(project: str, index: int, request: str) -> dict:
    """Просьба словами → план правок (см. instructions.parse)."""
    from .instructions import parse, timeline
    from .ffmpeg_utils import duration_of
    p = _Project(project)
    clip = p.clip(index)
    final = p.final(index)
    duration = duration_of(str(final)) if final.exists() else _clip_duration(clip)
    lines = timeline(p.transcript(), _clip_pieces(clip))
    return parse(p.cfg["highlights"]["ollama_model"], request, duration, lines)


def add_edits(project: str, index: int, ops: list[dict]) -> Path:
    """Добавляет разобранные правки к ролику и пересобирает его.

    «title» меняет заголовок (он вшит в кадр, поэтому чистый рендер делается
    заново), «narrate» добавляет реплику диктору, остальное — в список правок.
    """
    p = _Project(project)
    clip = p.clip(index)
    if any(op.get("op") != "title" for op in ops):
        p.ensure_clean(index)     # до записи: пока правок нет, готовый файл и есть чистый
    retitle = False
    for op in ops:
        kind = op.get("op")
        if kind == "title":
            clip["hook"] = op["text"][:120]
            retitle = True
        elif kind == "narrate":
            narr = clip.setdefault("narration", {"voice": p.cfg["tts"]["voice"],
                                                 "duck": True, "lines": []})
            narr.setdefault("lines", []).append({
                "start": _to_clean_time(float(op["at"]), clip.get("edits") or []),
                "text": op["text"]})
        elif kind == "image":
            # копия картинки рядом с проектом: путь без кириллицы и не пропадёт,
            # если оригинал переложат
            src = Path(op["file"])
            keep = p.work / f"edit_image_{index:02d}_{int(time.time())}{src.suffix.lower()}"
            shutil.copy2(src, keep)
            clip.setdefault("edits", []).append({**op, "file": str(keep)})
        else:
            clip.setdefault("edits", []).append(dict(op))
    if retitle:
        clean_path(p.work, index).unlink(missing_ok=True)
        if not has_extras(clip):
            tight = _ensure_tight(p.name, p.meta, p.cfg)
            render_clean(tight, p.transcript(), clip, index, p.cfg, p.work, p.final(index))
            p.save()
            return p.final(index)
    _log(f"Правки: {len(ops)} в {clip.get('file')}")
    return p.rebuild(index)


def clear_edits(project: str, index: int, narration_too: bool = True) -> Path:
    """Снимает правки (и диктора) — ролик возвращается к чистому рендеру."""
    p = _Project(project)
    clip = p.clip(index)
    clip.pop("edits", None)
    if narration_too:
        clip.pop("narration", None)
    return p.rebuild(index)
