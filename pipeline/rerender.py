"""Перерендер готового проекта после ручного исправления субтитров."""
import json
import time
from pathlib import Path

from .config import load_config, OUTPUT_DIR, WORK_DIR, INPUT_DIR
from .ffmpeg_utils import duration_of, video_size
from .punctuate import restore_punctuation
from .silence import cut_silences
from .smart_highlights import pick_highlights_smart
from .subtitles import build_ass
from .render import render_vertical, render_horizontal, pick_music


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def list_projects() -> list[str]:
    """Проекты из output/, у которых есть транскрипт и метаданные (можно править)."""
    if not OUTPUT_DIR.is_dir():
        return []
    return sorted(
        p.name for p in OUTPUT_DIR.iterdir()
        if (p / "transcript.json").exists() and (p / "metadata.json").exists()
    )


def load_segments(project: str) -> list[dict]:
    data = json.loads((OUTPUT_DIR / project / "transcript.json").read_text(encoding="utf-8"))
    return data["segments"]


def _retime_words(segment: dict, new_text: str) -> None:
    """Обновляет текст сегмента, сохраняя тайминги слов где возможно."""
    tokens = new_text.split()
    old_words = segment.get("words", [])
    if len(tokens) == len(old_words):
        for w, tok in zip(old_words, tokens):
            w["word"] = tok
    else:
        # число слов изменилось — распределяем время пропорционально длине слов
        start, end = segment["start"], segment["end"]
        total_chars = sum(len(t) for t in tokens) or 1
        cursor = start
        new_words = []
        for tok in tokens:
            dur = (end - start) * len(tok) / total_chars
            new_words.append({
                "start": round(cursor, 3),
                "end": round(min(cursor + dur, end), 3),
                "word": tok,
            })
            cursor += dur
        segment["words"] = new_words
    segment["text"] = new_text.strip()


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
        import shutil
        shutil.copy2(source, tight)
    return tight


def repick_and_render(project: str) -> Path:
    """Заново выбирает моменты по готовой расшифровке и перерендеривает шортсы."""
    import json as _json
    cfg = load_config()
    out_dir = OUTPUT_DIR / project
    transcript = _json.loads((out_dir / "transcript.json").read_text(encoding="utf-8"))
    meta = _json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))

    tight = _ensure_tight(project, meta, cfg)
    total = duration_of(str(tight))

    if cfg["punctuation"]["enabled"] and restore_punctuation(transcript):
        (out_dir / "transcript.json").write_text(
            _json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")

    sh = cfg["shorts"]
    clips, engine = pick_highlights_smart(
        transcript, total, sh["count"], sh["min_sec"], sh["max_sec"],
        sh["min_gap_sec"], cfg["highlights"]["ollama_model"])
    clips = [c for c in clips if c["end"] - c["start"] >= 5]
    _log(f"Выбрано клипов: {len(clips)} "
         f"({'нейросеть Ollama' if engine == 'ollama' else 'эвристика'})")

    # старые шортсы убираем, чтобы не осталось лишних файлов
    shorts_dir = out_dir / "shorts"
    for old in shorts_dir.glob("short_*.mp4"):
        old.unlink(missing_ok=True)

    work = WORK_DIR / project
    work.mkdir(parents=True, exist_ok=True)
    subs = cfg["subtitles"]
    vert = cfg["vertical"]
    music_cfg = cfg["music"]
    new_meta = []
    for i, clip in enumerate(clips, 1):
        ass_text = build_ass(
            transcript, clip["start"], clip["end"],
            vert["width"], vert["height"],
            subs["font"], subs["vertical_font_size"],
            subs["uppercase"], subs["max_words_per_card"],
            bottom_margin_ratio=0.30,
        )
        ass_file = work / f"short_{i:02d}.ass"
        ass_file.write_text(ass_text, encoding="utf-8")
        music = pick_music() if music_cfg["enabled"] else None
        dst = shorts_dir / f"short_{i:02d}.mp4"
        render_vertical(str(tight), clip["start"], clip["end"], str(ass_file),
                        str(dst), vert["width"], vert["height"], vert["background"],
                        music, music_cfg["volume"], cfg["render"]["shorts_max_mbps"])
        _log(f"Готов {dst.name} ({clip['end'] - clip['start']:.0f} с, "
             f"{dst.stat().st_size / 1024 / 1024:.0f} МБ)")
        new_meta.append({
            "file": f"shorts/{dst.name}",
            "start_sec": clip["start"], "end_sec": clip["end"],
            "hook": clip["hook"], "score": clip["score"],
        })

    meta["shorts"] = new_meta
    (out_dir / "metadata.json").write_text(
        _json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    _log(f"=== Переотбор завершён → {out_dir} ===")
    return out_dir


def apply_corrections(project: str, new_texts: list[str]) -> Path:
    """Записывает исправленные тексты сегментов и перерендеривает все ролики."""
    cfg = load_config()
    out_dir = OUTPUT_DIR / project
    transcript = json.loads((out_dir / "transcript.json").read_text(encoding="utf-8"))
    meta = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))

    segments = transcript["segments"]
    if len(new_texts) != len(segments):
        raise ValueError(
            f"Строк {len(new_texts)}, а сегментов {len(segments)} — "
            "нельзя удалять или добавлять строки, только править текст."
        )
    changed = 0
    for seg, text in zip(segments, new_texts):
        if text.strip() and text.strip() != seg["text"]:
            _retime_words(seg, text)
            changed += 1
    _log(f"Исправлено сегментов: {changed}")

    (out_dir / "transcript.json").write_text(
        json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")

    tight = _ensure_tight(project, meta, cfg)
    work = WORK_DIR / project
    subs = cfg["subtitles"]
    vert = cfg["vertical"]
    music_cfg = cfg["music"]

    for i, clip in enumerate(meta.get("shorts", []), 1):
        _log(f"Перерендериваю shorts/short_{i:02d}.mp4...")
        ass_text = build_ass(
            transcript, clip["start_sec"], clip["end_sec"],
            vert["width"], vert["height"],
            subs["font"], subs["vertical_font_size"],
            subs["uppercase"], subs["max_words_per_card"],
            bottom_margin_ratio=0.30,
        )
        ass_file = work / f"short_{i:02d}.ass"
        ass_file.write_text(ass_text, encoding="utf-8")
        music = pick_music() if music_cfg["enabled"] else None
        render_vertical(str(tight), clip["start_sec"], clip["end_sec"], str(ass_file),
                        str(out_dir / clip["file"]), vert["width"], vert["height"],
                        vert["background"], music, music_cfg["volume"],
                        cfg["render"]["shorts_max_mbps"])

    if cfg["render_youtube_version"]:
        _log("Перерендериваю полную версию 16:9...")
        total = duration_of(str(tight))
        w, h = video_size(str(tight))
        ass_text = build_ass(
            transcript, 0.0, total, w, h,
            subs["font"], subs["horizontal_font_size"],
            uppercase=False, max_words=6, bottom_margin_ratio=0.06,
        )
        ass_full = work / "full.ass"
        ass_full.write_text(ass_text, encoding="utf-8")
        render_horizontal(str(tight), str(ass_full),
                          str(out_dir / f"{project}_youtube.mp4"),
                          cfg["render"]["youtube_max_mbps"])

    _log(f"=== Перерендер завершён → {out_dir} ===")
    return out_dir
