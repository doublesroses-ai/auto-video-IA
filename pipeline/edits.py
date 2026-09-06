"""Правки готового ролика: вырезать кусок, вставить картинку, анимация
в начале и в финале, надпись на экране.

Каждая правка — один проход ffmpeg по уже готовому файлу. Так проще и надёжнее,
чем встраивать всё в общий граф рендера: правок обычно одна-две, ролик короткий,
а каждый шаг можно проверить отдельно. Список правок хранится в metadata.json
и заново накладывается при любом пересборе ролика.
"""
import os
from pathlib import Path

from .ffmpeg_utils import ffmpeg, run, probe, filter_path, video_encoder_args

FONT_FILES = ("ariblk.ttf", "arialbd.ttf", "arial.ttf")
CARD_SEC = 2.5        # заставка с надписью в начале или в конце
ZOOM_SEC = 2.0        # наезд камеры
FADE_SEC = 1.0        # затемнение
SEAM = 0.04           # микрофейд звука на стыке после вырезания
DEFAULT_OUTRO = "ПОДПИШИСЬ"
DEFAULT_INTRO = ""

OPS = ("cut", "trim", "image", "intro", "outro", "text")
STYLES = ("card", "zoom", "fade")
PLACES = ("top", "center", "bottom")


def _font_file() -> str | None:
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in FONT_FILES:
        if (fonts / name).exists():
            return str(fonts / name)
    return None


def _info(path: str | Path) -> dict:
    data = probe(str(path))
    out = {"dur": float(data["format"]["duration"]), "fps": 30.0, "w": 1080, "h": 1920}
    for s in data["streams"]:
        if s.get("codec_type") == "video":
            out["w"], out["h"] = int(s["width"]), int(s["height"])
            num, _, den = str(s.get("r_frame_rate", "30/1")).partition("/")
            try:
                out["fps"] = float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                pass
            break
    return out


def _wrap(text: str, width: int) -> str:
    """Перенос строк для drawtext — сам он переносить не умеет."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def _drawtext(text_file: Path, fontsize: int, x: str, y: str,
              alpha: str = "1", enable: str = "", box: bool = False) -> str:
    font = _font_file()
    args = [f"textfile='{filter_path(text_file)}'"]
    if font:
        args.append(f"fontfile='{filter_path(font)}'")
    args += [f"fontsize={fontsize}", "fontcolor=white",
             "borderw=6", "bordercolor=black@0.75",
             f"line_spacing={fontsize // 6}", "text_align=center",
             f"x='{x}'", f"y='{y}'", f"alpha='{alpha}'"]
    if box:
        args += ["box=1", "boxcolor=black@0.55", f"boxborderw={fontsize // 3}"]
    if enable:
        args.append(f"enable='{enable}'")
    return "drawtext=" + ":".join(args)


def _encode(cmd_inputs: list[str], graph: str, dst: str | Path, total: float,
            max_mbps: float | None, copy_audio: bool = False) -> None:
    cmd = [ffmpeg(), "-y", *cmd_inputs, "-filter_complex", graph,
           "-map", "[vout]", *video_encoder_args(max_mbps)]
    if copy_audio:
        cmd += ["-map", "0:a", "-c:a", "copy"]
    else:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-t", f"{total:.3f}", "-movflags", "+faststart", str(dst)]
    run(cmd, desc="правка ролика")


# ---------------------------------------------------------------- операции

def _op_trim(src, dst, start: float, end: float, info, max_mbps) -> float:
    start = max(0.0, float(start))
    end = min(info["dur"], float(end))
    if end - start < 1.0:
        raise ValueError("После обрезки от ролика осталось бы меньше секунды")
    graph = (f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[vout];"
             f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[aout]")
    _encode(["-i", str(src)], graph, dst, end - start, max_mbps)
    return end - start


def _op_cut(src, dst, frm: float, to: float, info, max_mbps) -> float:
    dur = info["dur"]
    frm = max(0.0, float(frm))
    to = min(dur, float(to))
    if to - frm < 0.2:
        raise ValueError("Фрагмент для вырезания короче 0,2 с")
    if frm <= 0.05 and to >= dur - 0.05:
        raise ValueError("Нельзя вырезать весь ролик целиком")
    if frm <= 0.05:
        return _op_trim(src, dst, to, dur, info, max_mbps)
    if to >= dur - 0.05:
        return _op_trim(src, dst, 0.0, frm, info, max_mbps)
    graph = (
        f"[0:v]trim=end={frm:.3f},setpts=PTS-STARTPTS[v0];"
        f"[0:a]atrim=end={frm:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={frm - SEAM:.3f}:d={SEAM}[a0];"
        f"[0:v]trim=start={to:.3f},setpts=PTS-STARTPTS[v1];"
        f"[0:a]atrim=start={to:.3f},asetpts=PTS-STARTPTS,afade=t=in:st=0:d={SEAM}[a1];"
        "[v0][a0][v1][a1]concat=n=2:v=1:a=1[vout][aout]"
    )
    total = dur - (to - frm)
    _encode(["-i", str(src)], graph, dst, total, max_mbps)
    return total


def _op_image(src, dst, file: str, at, seconds: float, info, max_mbps) -> float:
    if not file or not Path(file).is_file():
        raise ValueError("Не указан файл картинки")
    w, h, fps, dur = info["w"], info["h"], info["fps"], info["dur"]
    seconds = min(max(float(seconds or 3.0), 1.0), 15.0)
    n_frames = max(int(round(seconds * fps)), 2)
    # картинка на размытой подложке, как в вертикальном кадре, и медленный наезд
    img = (
        "[1:v]split[bg][fg];"
        f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=22:2[b];"
        f"[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[f];"
        f"[b][f]overlay=(W-w)/2:(H-h)/2,scale={2 * w}:{2 * h},"
        f"zoompan=z='1+0.10*on/{n_frames}':d=1:x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
        f":s={w}x{h}:fps={fps:g},format=yuv420p,setsar=1,"
        f"trim=duration={seconds:.3f},setpts=PTS-STARTPTS[img];"
        f"[2:a]aformat=sample_rates=48000:channel_layouts=stereo,"
        f"atrim=duration={seconds:.3f},asetpts=PTS-STARTPTS[sil];"
        "[0:v]format=yuv420p,setsar=1[v];"
        "[0:a]aformat=sample_rates=48000:channel_layouts=stereo[a];"
    )
    if at == "start":
        graph = img + "[img][sil][v][a]concat=n=2:v=1:a=1[vout][aout]"
    elif at == "end":
        graph = img + "[v][a][img][sil]concat=n=2:v=1:a=1[vout][aout]"
    else:
        x = min(max(float(at), 0.0), dur)
        if x < 0.05:
            return _op_image(src, dst, file, "start", seconds, info, max_mbps)
        if x > dur - 0.05:
            return _op_image(src, dst, file, "end", seconds, info, max_mbps)
        graph = img + (
            "[v]split[va][vb];[a]asplit[aa][ab];"
            f"[va]trim=end={x:.3f},setpts=PTS-STARTPTS[v1];"
            f"[aa]atrim=end={x:.3f},asetpts=PTS-STARTPTS[a1];"
            f"[vb]trim=start={x:.3f},setpts=PTS-STARTPTS[v2];"
            f"[ab]atrim=start={x:.3f},asetpts=PTS-STARTPTS[a2];"
            "[v1][a1][img][sil][v2][a2]concat=n=3:v=1:a=1[vout][aout]"
        )
    inputs = ["-i", str(src),
              "-loop", "1", "-framerate", f"{fps:g}", "-t", f"{seconds:.3f}", "-i", str(file),
              "-f", "lavfi", "-t", f"{seconds:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    total = dur + seconds
    _encode(inputs, graph, dst, total, max_mbps)
    return total


def _card_text_file(work: Path, name: str, text: str, width: int) -> Path:
    path = Path(work) / f"{name}.txt"
    # newline обязателен: Windows пишет \r\n, и drawtext рисует \r пустой строкой
    path.write_text(_wrap(text, width), encoding="utf-8", newline="\n")
    return path


def _op_outro(src, dst, style: str, text: str, info, max_mbps, work: Path) -> float:
    w, h, fps, dur = info["w"], info["h"], info["fps"], info["dur"]
    if style == "fade":
        st = max(dur - FADE_SEC, 0)
        graph = (f"[0:v]fade=t=out:st={st:.3f}:d={FADE_SEC}[vout];"
                 f"[0:a]afade=t=out:st={st:.3f}:d={FADE_SEC}[aout]")
        _encode(["-i", str(src)], graph, dst, dur, max_mbps)
        return dur
    if style == "zoom":
        n0 = int(max(dur - ZOOM_SEC, 0) * fps)
        n1 = max(int(ZOOM_SEC * fps), 1)
        st = max(dur - 0.7, 0)
        # fps перед zoompan обязателен: он раскладывает кадры по своему счётчику,
        # и запись с телефона с плавающей частотой кадров укорачивалась на полсекунды
        graph = (f"[0:v]fps={fps:g},scale={2 * w}:{2 * h},"
                 f"zoompan=z='if(gte(on,{n0}),min(1+0.25*(on-{n0})/{n1},1.25),1)':d=1"
                 f":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={w}x{h}:fps={fps:g},"
                 f"fade=t=out:st={st:.3f}:d=0.7[vout];"
                 f"[0:a]afade=t=out:st={st:.3f}:d=0.7[aout]")
        _encode(["-i", str(src)], graph, dst, dur, max_mbps)
        return dur
    # card: стоп-кадр размывается и темнеет, надпись выезжает снизу
    text = (text or DEFAULT_OUTRO).strip()
    tf = _card_text_file(work, "outro_text", text, 16)
    total = dur + CARD_SEC
    t0 = f"{dur:.3f}"
    graph = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={CARD_SEC},"
        f"boxblur=18:2:enable='gte(t,{t0})',"
        f"drawbox=c=black@0.45:t=fill:enable='gte(t,{t0})',"
        + _drawtext(tf, w // 10, "(w-tw)/2",
                    f"(h-th)/2-(1-min((t-{t0})/0.6,1))*{h // 20}",
                    alpha=f"min((t-{t0})/0.5,1)", enable=f"gte(t,{t0})")
        + f",fade=t=out:st={total - 0.6:.3f}:d=0.6[vout];"
        f"[0:a]apad=pad_dur={CARD_SEC},afade=t=out:st={max(dur - 0.3, 0):.3f}:d=1.2[aout]"
    )
    _encode(["-i", str(src)], graph, dst, total, max_mbps)
    return total


def _op_intro(src, dst, style: str, text: str, info, max_mbps, work: Path) -> float:
    w, h, fps, dur = info["w"], info["h"], info["fps"], info["dur"]
    if style == "fade":
        graph = (f"[0:v]fade=t=in:st=0:d={FADE_SEC}[vout];"
                 f"[0:a]afade=t=in:st=0:d={FADE_SEC}[aout]")
        _encode(["-i", str(src)], graph, dst, dur, max_mbps)
        return dur
    if style == "zoom":
        n1 = max(int(ZOOM_SEC * fps), 1)
        # fps перед zoompan обязателен: он раскладывает кадры по своему счётчику,
        # и запись с телефона с плавающей частотой кадров укорачивалась на полсекунды
        graph = (f"[0:v]fps={fps:g},scale={2 * w}:{2 * h},"
                 f"zoompan=z='if(lt(on,{n1}),1.25-0.25*on/{n1},1)':d=1"
                 f":x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':s={w}x{h}:fps={fps:g}[vout];"
                 "[0:a]anull[aout]")
        _encode(["-i", str(src)], graph, dst, dur, max_mbps)
        return dur
    text = (text or DEFAULT_INTRO).strip()
    total = dur + CARD_SEC
    delay = int(CARD_SEC * 1000)
    chain = (f"[0:v]tpad=start_mode=clone:start_duration={CARD_SEC},"
             f"boxblur=18:2:enable='lt(t,{CARD_SEC})',"
             f"drawbox=c=black@0.45:t=fill:enable='lt(t,{CARD_SEC})'")
    if text:
        tf = _card_text_file(work, "intro_text", text, 16)
        chain += "," + _drawtext(tf, w // 10, "(w-tw)/2",
                                 f"(h-th)/2-(1-min(t/0.6,1))*{h // 20}",
                                 alpha=f"min(t/0.5,1)", enable=f"lt(t,{CARD_SEC})")
    graph = (chain + "[vout];"
             f"[0:a]adelay={delay}|{delay},afade=t=in:st={CARD_SEC}:d=0.5[aout]")
    _encode(["-i", str(src)], graph, dst, total, max_mbps)
    return total


def _op_text(src, dst, text: str, at: float, seconds: float, where: str,
             info, max_mbps, work: Path) -> float:
    w, h, dur = info["w"], info["h"], info["dur"]
    text = (text or "").strip()
    if not text:
        raise ValueError("Пустая надпись")
    a = min(max(float(at or 0.0), 0.0), max(dur - 0.5, 0))
    b = min(a + min(max(float(seconds or 3.0), 0.8), 30.0), dur)
    tf = _card_text_file(work, f"text_{int(a * 10)}", text, 22)
    y = {"top": "h*0.10", "bottom": "h*0.78-th"}.get(where, "(h-th)/2")
    alpha = (f"if(lt(t,{a:.3f}+0.4),(t-{a:.3f})/0.4,"
             f"if(gt(t,{b:.3f}-0.4),({b:.3f}-t)/0.4,1))")
    graph = ("[0:v]" + _drawtext(tf, w // 16, "(w-tw)/2", y, alpha=alpha,
                                 enable=f"between(t,{a:.3f},{b:.3f})", box=True)
             + "[vout]")
    _encode(["-i", str(src)], graph, dst, dur, max_mbps, copy_audio=True)
    return dur


# ---------------------------------------------------------------- применение

def describe(op: dict) -> str:
    """Правка по-русски — чтобы показать план перед применением."""
    kind = op.get("op")
    if kind == "cut":
        return f"вырезать фрагмент {_mmss(op['from'])}–{_mmss(op['to'])}"
    if kind == "trim":
        return f"оставить только отрезок {_mmss(op['start'])}–{_mmss(op['end'])}"
    if kind == "image":
        at = op.get("at")
        place = {"start": "в начало", "end": "в конец"}.get(at, f"на {_mmss(at)}")
        name = Path(op["file"]).name if op.get("file") else "(файл выберешь дальше)"
        return f"вставить картинку {name} {place} на {float(op.get('seconds', 3)):g} с"
    if kind in ("intro", "outro"):
        where = "в начале" if kind == "intro" else "в финале"
        style = {"card": "заставка с надписью", "zoom": "наезд камеры",
                 "fade": "затемнение"}[op.get("style", "card")]
        text = op.get("text") or (DEFAULT_OUTRO if kind == "outro" else "")
        extra = f" «{text}»" if op.get("style", "card") == "card" and text else ""
        return f"{style}{extra} {where}"
    if kind == "text":
        where = {"top": "вверху", "bottom": "внизу"}.get(op.get("where"), "по центру")
        return (f"надпись «{op['text']}» {where} с {_mmss(op.get('at', 0))} "
                f"на {float(op.get('seconds', 3)):g} с")
    if kind == "narrate":
        return f"диктор говорит «{op['text']}» на {_mmss(op.get('at', 0))}"
    if kind == "title":
        return f"заголовок ролика: «{op['text']}»"
    return str(op)


def _mmss(sec) -> str:
    try:
        sec = float(sec)
    except (TypeError, ValueError):
        return "?"
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def apply_edits(src: str | Path, dst: str | Path, edits: list[dict], work: Path,
                max_mbps: float | None = 8) -> float:
    """Накладывает правки по очереди. Возвращает длительность результата."""
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    cur = Path(src)
    tmp_files = []
    dur = _info(cur)["dur"]
    for n, op in enumerate(edits):
        info = _info(cur)
        out = work / f"edit_step_{n:02d}.mp4"
        kind = op.get("op")
        if kind == "cut":
            dur = _op_cut(cur, out, op["from"], op["to"], info, max_mbps)
        elif kind == "trim":
            dur = _op_trim(cur, out, op["start"], op["end"], info, max_mbps)
        elif kind == "image":
            dur = _op_image(cur, out, op.get("file", ""), op.get("at", "start"),
                            op.get("seconds", 3), info, max_mbps)
        elif kind == "outro":
            dur = _op_outro(cur, out, op.get("style", "card"), op.get("text", ""),
                            info, max_mbps, work)
        elif kind == "intro":
            dur = _op_intro(cur, out, op.get("style", "card"), op.get("text", ""),
                            info, max_mbps, work)
        elif kind == "text":
            dur = _op_text(cur, out, op.get("text", ""), op.get("at", 0),
                           op.get("seconds", 3), op.get("where", "center"),
                           info, max_mbps, work)
        else:
            continue
        cur = out
        tmp_files.append(out)
    if cur == Path(src):
        import shutil
        shutil.copy2(src, dst)
    else:
        import shutil
        shutil.move(str(cur), str(dst))
    for f in tmp_files:
        Path(f).unlink(missing_ok=True)
    return dur
