"""Полный конвейер обработки одного видео: паузы → речь → шортсы → рендер."""
import json
import shutil
import time
from pathlib import Path

from .config import load_config, shorts_count, OUTPUT_DIR, WORK_DIR
from .ffmpeg_utils import duration_of, video_size, has_audio
from .silence import cut_silences
from .transcribe import transcribe
from .av_signals import measure
from .punctuate import restore_punctuation
from .smart_highlights import pick_highlights_smart
from .subtitles import build_ass
from .render import render_horizontal
from .shorts import render_short, _clip_pieces, _clip_duration, _empty_ass  # noqa: F401


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def process_video(src: str | Path, config: dict | None = None) -> Path:
    """Обрабатывает одно видео, возвращает папку с результатами."""
    cfg = config or load_config()
    src = Path(src)
    stem = src.stem
    out_dir = OUTPUT_DIR / stem
    shorts_dir = out_dir / "shorts"
    work = WORK_DIR / stem
    for d in (out_dir, shorts_dir, work):
        d.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    _log(f"=== Обработка: {src.name} ===")

    if not has_audio(src):
        raise RuntimeError(f"В видео нет звуковой дорожки: {src.name}")

    # 1. Вырезаем паузы
    tight = work / "tight.mp4"
    sil = cfg["silence"]
    if sil["enabled"]:
        _log("Шаг 1/6: вырезаю паузы и тишину...")
        stats = cut_silences(str(src), str(tight), work,
                             sil["noise_db"], sil["min_silence_sec"], sil["pad_sec"],
                             cfg["render"]["master_max_mbps"])
        _log(f"  было {stats['original_sec']:.0f} с, стало {stats['kept_sec']:.0f} с "
             f"({stats['segments']} фрагментов)")
    else:
        shutil.copy2(src, tight)
        _log("Шаг 1/6: обрезка пауз выключена, пропускаю")

    # 2. Распознаём речь
    _log("Шаг 2/6: распознаю речь (Whisper)...")
    transcript = transcribe(str(tight), work,
                            cfg["whisper_model"], cfg["language"],
                            cfg.get("vocabulary"))
    n_words = sum(len(s.get("words", [])) for s in transcript["segments"])
    _log(f"  язык: {transcript['language']}, слов: {n_words}")

    if cfg["punctuation"]["enabled"]:
        if restore_punctuation(transcript):
            (work / "transcript.json").write_text(
                json.dumps(transcript, ensure_ascii=False, indent=1), encoding="utf-8")

    total = duration_of(str(tight))

    # 3. Смотрим на саму картинку и звук: где экран застыл, где эмоции
    signals = None
    if cfg["av_signals"]["enabled"]:
        _log("Шаг 3/6: смотрю, где экран застыл и где всплески эмоций...")
        signals = measure(str(tight), work, total)
        if signals.ok:
            _log(f"  зон застывшего экрана: {len(signals.freeze_zones)}"
                 + (", видео статично по своей природе — штраф отключён"
                    if signals.static_video else ""))
        else:
            _log("  измерить не вышло, отбор пойдёт только по тексту")

    # 4. Выбираем лучшие моменты
    _log("Шаг 4/6: выбираю моменты для шортсов...")
    sh = cfg["shorts"]
    limit, exact = shorts_count(cfg, total)
    clips, engine = pick_highlights_smart(
        transcript, total, limit, sh["min_sec"], sh["max_sec"],
        sh["min_gap_sec"], cfg["highlights"]["ollama_model"], signals, cfg, exact)
    clips = [c for c in clips if _clip_duration(c) >= 5]
    _log(f"  выбрано клипов: {len(clips)} "
         f"({'нейросеть Ollama' if engine == 'ollama' else 'эвристика'})")

    subs = cfg["subtitles"]

    # 5. Рендерим шортсы
    _log("Шаг 5/6: рендерю шортсы 9:16...")
    meta = []
    for i, clip in enumerate(clips, 1):
        pieces = _clip_pieces(clip)
        entry = {
            "file": f"shorts/short_{i:02d}.mp4",
            "pieces": pieces,
            "start_sec": pieces[0][0], "end_sec": pieces[-1][1],
            "hook": clip["hook"], "score": clip["score"],
        }
        dst = render_short(tight, transcript, entry, i, cfg, work, out_dir)
        _log(f"  готов {dst.name} ({_clip_duration(clip):.0f} с"
             + (f", склейка из {len(pieces)} кусков" if len(pieces) > 1 else "")
             + f", {dst.stat().st_size / 1024 / 1024:.0f} МБ)")
        meta.append(entry)

    # 6. Полная версия 16:9 для YouTube
    if cfg["render_youtube_version"]:
        _log("Шаг 6/6: рендерю полную версию 16:9 с субтитрами...")
        w, h = video_size(str(tight))
        ass_text = build_ass(
            transcript, 0.0, total, w, h,
            subs["font"], subs["horizontal_font_size"],
            uppercase=False, max_words=6, bottom_margin_ratio=0.06,
        )
        ass_full = work / "full.ass"
        ass_full.write_text(ass_text, encoding="utf-8")
        render_horizontal(str(tight), str(ass_full), str(out_dir / f"{stem}_youtube.mp4"),
                          cfg["render"]["youtube_max_mbps"])
        _log(f"  готов {stem}_youtube.mp4")
    else:
        _log("Шаг 6/6: версия 16:9 выключена в конфиге")

    (out_dir / "metadata.json").write_text(
        json.dumps({"source": src.name, "language": transcript["language"],
                    "shorts": meta}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    shutil.copy2(work / "transcript.json", out_dir / "transcript.json")

    if not cfg["keep_work_files"]:
        shutil.rmtree(work, ignore_errors=True)

    _log(f"=== Готово за {(time.time() - t0) / 60:.1f} мин → {out_dir} ===")
    return out_dir
