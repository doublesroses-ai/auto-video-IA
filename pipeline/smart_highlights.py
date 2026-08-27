"""Умный отбор моментов через локальную нейросеть (Ollama).

Как это работает:
  1. Расшифровка режется на окна по несколько минут, и по каждому окну модель
     ищет сильные моменты. Одним запросом на всё видео она читала только начало
     и не видела финала — самого ценного в стриме.
  2. Кандидаты подгоняются кодом: конец переносится на законченную фразу,
     мусорное начало («ну, вот, короче») срезается, длительность вводится в рамки.
     Модель эти правила игнорирует, поэтому их добивает код. Отдельная забота —
     вялое начало: если ролик открывается перечислением («Площадка, космодром,
     энергия»), код ищет рядом — и назад, и вперёд — фразу, с которой ролик
     зазвучит, и заново подгоняет конец.
  3. Картинка и звук штрафуют кандидатов с застывшим экраном и добавляют вес
     эмоциональным местам.
  4. Судья одним запросом сравнивает всех выживших между собой. Маленькая модель
     плохо оценивает клип в отрыве, но хорошо сравнивает несколько.

Если Ollama недоступна или ответ кривой — тихо откатываемся на эвристику.
"""
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from .highlights import _split_sentences, pick_highlights

OLLAMA_URL = "http://localhost:11434"

CHUNK_SENTENCES = 38      # окно разведки, примерно 2-4 минуты речи
CHUNK_OVERLAP = 5         # перекрытие, чтобы момент не разорвался на границе
PER_CHUNK = 4             # сколько моментов просить из одного окна
# Просить 4 вместо 2 стоит восьми секунд на всё видео, зато в список попадают
# сюжеты, которых программа раньше просто не видела. Менять только вместе
# с лимитом ответа в _scout: при четырёх моментах в 250 токенов ответ не влезает
# и окно теряется целиком.

# слова, с которых не должен начинаться ролик
_FILLER = (r"(?:э+|а+|м+|ну|вот|короче|так|ладно|значит|это|типа|как\s*бы|"
           r"в\s*общем|слушай|смотри|давай|окей|ага|угу|да)")
_FILLER_HEAD = re.compile(rf"^(?:\W*\b{_FILLER}\b[\s,.\-–—]*)+", re.IGNORECASE)
_FILLER_WORD = re.compile(rf"^\W*{_FILLER}\W*$", re.IGNORECASE)

MAX_FILLER_TRIM_SEC = 1.5
MAX_FILLER_WORDS = 2


# ---------------------------------------------------------------- сервер

def _server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3):
            return True
    except Exception:
        return False


def _start_server() -> bool:
    """Поднимает сервер Ollama, если он установлен, но не запущен."""
    exe = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
    if not exe.is_file():
        return False
    try:
        subprocess.Popen([str(exe)], creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        return False
    for _ in range(12):
        time.sleep(1)
        if _server_up():
            return True
    return False


def ollama_available(model: str, autostart: bool = True) -> bool:
    if not _server_up():
        if not autostart or not _start_server():
            return False
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        return any(n == model or n.startswith(model + ":")
                   or n.split(":")[0] == model.split(":")[0] for n in names)
    except Exception:
        return False


def _ollama_chat(model: str, prompt: str, num_predict: int,
                 timeout: float = 300.0) -> str:
    """Один запрос к модели. num_predict обязателен: без потолка модель однажды
    зациклилась и молотила пять минут."""
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        # think=false: у «думающих» моделей (qwen3) размышления вслух занимают минуты
        "think": False,
        "keep_alive": "15m",
        "options": {
            "num_ctx": 8192,          # окно разведки в него влезает с запасом
            "num_predict": num_predict,
            "temperature": 0.15,
        },
    }
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))["message"]["content"]


# ---------------------------------------------------------------- промпты

def _scout_prompt(sentences: list[dict], lo: int, hi: int,
                  min_sec: float, max_sec: float) -> str:
    listing = "\n".join(
        f"[{i}] ({sentences[i]['end'] - sentences[i]['start']:.0f} с) {sentences[i]['text']}"
        for i in range(lo, hi)
    )
    return f"""Ты монтажёр вертикальных роликов для TikTok и YouTube Shorts.
Ниже — кусок расшифровки видео: пронумерованные фразы с длительностью.
Найди в ЭТОМ куске до {PER_CHUNK} моментов, из которых получится самостоятельный ролик.

Момент — непрерывный диапазон фраз [start_id..end_id] длиной {min_sec:.0f}-{max_sec:.0f} секунд.

Что делает момент сильным:
- в нём что-то ПРОИСХОДИТ: результат, провал, неожиданность, спор, признание,
  удивившая цифра, сильная эмоция;
- он понятен человеку, который не видел остального видео;
- он ЗАКАНЧИВАЕТСЯ: вывод, реакция, итог. Не обрывается на полуслове.

Что моментом НЕ является (не бери такое):
- экскурсия и перечисление: «тут у меня это, тут вон то, а вот здесь ещё»;
- комментарий к картинке: «смотрите сюда», «вот эта штука» — зритель не поймёт, о чём речь;
- бормотание и мысли вслух без вывода.

Границы:
- фраза start_id — первое, что услышит зритель. В ней обязано быть действие,
  заявление, вопрос или эмоция. Хорошее начало:
    «Я разочарован, так приземлиться на разрушенную планету нельзя»
    «Я главного персонажа не взял»
    «Стоило поставить одну рельсовую пушку, и вопросов больше нет»
- НЕЛЬЗЯ начинать с перечисления вещей через запятую. Плохое начало:
    «Площадка, космодром, энергия, энергетическая база, мини реактор»
    «Много, уголь с продуктивностью 520 с модулем продуктивности»
- НЕЛЬЗЯ начинать с «ну», «вот», «так», «короче», «ладно», «значит», «то есть»,
  «эээ» и с обрывка фразы: «кончился для производства фундамента».
- Прочитай фразу start_id целиком. Не подходит — бери следующую фразу,
  даже если момент станет короче.
- end_id — фраза, на которой мысль закончена.

Если в этом куске нет ничего сильного, верни пустой список — это нормально
и лучше, чем притянутый за уши момент.

Для каждого момента укажи:
  start_id, end_id
  strength — 1..10, насколько это зацепит человека, впервые увидевшего ролик
  about — о чём момент, одним предложением
  ending — чем он заканчивается, до 10 слов

Ответь строго JSON:
{{"moments":[{{"start_id":12,"end_id":18,"strength":8,"about":"...","ending":"..."}}]}}

Фразы:
{listing}
"""


def _judge_prompt(candidates: list[dict]) -> str:
    # 450 символов вместо 700 и без поля с пояснением: на дюжине кандидатов
    # ответ упирался в лимит и обрывался на полуслове, а вместо заголовков
    # в ролики попадали сырые куски расшифровки
    listing = "\n\n".join(
        f"[{n}] ({c['duration']:.0f} с) {c['text'][:450]}"
        for n, c in enumerate(candidates)
    )
    return f"""Ты редактор коротких роликов. Ниже — кандидаты в шортсы, каждый под номером.

Расставь их по силе: какой зацепит зрителя, впервые увидевшего ролик, а какой нет.
Сильный: что-то происходит, понятен без контекста, заканчивается выводом или реакцией.
Слабый: перечисление, комментарий к картинке, обрыв на полуслове, много отсылок
к тому, чего зритель не видел.

Для каждого поставь оценку 1..10.

Если несколько кандидатов про ОДНО И ТО ЖЕ, высокую оценку дай только лучшему
из них: подборка из похожих роликов зрителю неинтересна.

Ответь строго JSON:
{{"ranking":[{{"id":0,"score":9}}]}}

Кандидаты:
{listing}
"""


# ---------------------------------------------------------------- подгонка

def _fit_end(sentences: list[dict], i: int, j: int,
             min_sec: float, max_sec: float) -> int | None:
    """Двигает конец клипа на законченную фразу внутри лимитов длительности."""
    start = sentences[i]["start"]

    # если вылезли за максимум — отходим назад
    while j > i and sentences[j]["end"] - start > max_sec:
        j -= 1
    # если не добрали минимум — тянем вперёд
    while j + 1 < len(sentences) and sentences[j]["end"] - start < min_sec:
        if sentences[j + 1]["end"] - start > max_sec:
            break
        j += 1
    if sentences[j]["end"] - start > max_sec:
        return None

    # предпочитаем закончить на фразе, которая действительно завершена
    if not sentences[j].get("strong"):
        forward = j
        while forward + 1 < len(sentences):
            forward += 1
            if sentences[forward]["end"] - start > max_sec:
                break
            if sentences[forward].get("strong"):
                return forward
        back = j
        while back > i:
            back -= 1
            if sentences[back]["end"] - start < min_sec * 0.8:
                break
            if sentences[back].get("strong"):
                return back
    return j


# Слова, с которых фраза не может начинаться: это середина мысли.
# Союзы и слова-отсылки выдают обрубок вернее, чем «ну» и «вот».
_FRAGMENT_START = {
    "что", "чтобы", "который", "которая", "которое", "которые", "тоже", "также",
    "потому", "поэтому", "хотя", "если", "когда", "пока", "чем", "либо",
    "а", "и", "но", "или", "же", "ведь", "зато", "причём", "притом",
    # наречия-усилители тоже почти всегда стоят в середине мысли
    "очень", "просто", "прям", "прямо", "именно", "особенно", "довольно", "весьма",
}


_FIRST_PERSON = {"я", "мы", "меня", "мне", "нас", "нам", "мой", "моя", "моё", "наш",
                 "мои", "нашей", "моего"}
_NEGATION = {"не", "нет", "нельзя", "никак", "никогда", "ничего"}
_HEAD_WORDS = 8           # смотрим только начало фразы — зритель уходит на нём


def _opening_text(sentence: dict) -> str:
    """Текст фразы таким, каким его УСЛЫШАТ: без «ну», «вот», «так» в начале.

    Эти слова всё равно срезает _trim_filler перед рендером, а оценке они
    мешали: «Так, вы есть на списке?» выглядело вяло только из-за «Так».
    Если под паразитом оголяется союз, чистку отменяем — ровно как в _trim_filler.
    """
    text = (sentence.get("text") or "").strip()
    rest = _FILLER_HEAD.sub("", text).strip()
    if not rest:
        return text
    head = re.match(r"[\w-]+", rest)
    if head and head.group(0).lower() in _FRAGMENT_START:
        return text
    return rest


def _opening_score(sentence: dict) -> float:
    """Насколько фраза годится в НАЧАЛО ролика. 0 — вяло, 10 — отлично.

    Главная беда — перечисления: «Площадка, космодром, энергия, энергетическая
    база», «Много, уголь с продуктивностью 520». Формально это законченные
    фразы, но зритель не понимает, к чему это, и листает дальше.

    Отличие видно по одному месту — что стоит ДО ПЕРВОЙ ЗАПЯТОЙ. В сильном
    начале там целое высказывание («Я разочарован»), в перечислении — одинокое
    слово («Много,», «Площадка,»), за которым идёт список. Раньше здесь
    вдобавок считались глаголы по окончаниям, и счётчик врал: «продуктивность»,
    «безопасность» и «жило» он принимал за глаголы, из-за чего оба вялых начала
    проходили проверку. Угадывание глаголов убрано целиком.
    """
    text = _opening_text(sentence)
    words = re.findall(r"[\w-]+", text.lower())
    if not words:
        return 0.0
    score = 5.0

    # 1. сколько слов до первой запятой
    first = re.findall(r"[\w-]+", re.split(r"[,;:]", text)[0])
    if len(first) <= 1:
        score -= 3.0          # «Много,» / «Площадка,» — затравка перечня
    elif len(first) == 2:
        score -= 0.5
    elif len(first) >= 4:
        score += 1.0

    # 2. запятые в первых восьми словах: чем гуще, тем вернее это список
    marks = list(re.finditer(r"[\w-]+", text))
    head_end = marks[min(len(marks), _HEAD_WORDS) - 1].end()
    score -= 1.2 * min(text[:head_end].count(","), 3)

    if _starts_mid_thought(sentence):
        score -= 3.0
    # первое лицо ищем в начале фразы, а не строго первым словом:
    # «У меня 5000 пакетов» — такое же личное заявление, как «Я разочарован»
    if set(words[:3]) & _FIRST_PERSON:
        score += 2.0
    if set(words[:6]) & _NEGATION:
        score += 1.0          # отрицание почти всегда заявление
    if re.search(r"[?!]", text):
        score += 1.5
    if len(words) < 4:
        score -= 2.0      # «Не верю короче.» — слишком мало, чтобы зацепить

    # Фраза с маленькой буквы — обрубок чужой мысли: расстановщик знаков
    # начинает предложение с большой. «кончился для производства фундамента»,
    # «скорости меня раздавят секундой» — с такого ролик начинаться не должен.
    # Это потолок, а не штраф: иначе обрубок набирал проходной балл на «я» и «не».
    raw = (sentence.get("text") or "").lstrip()
    if raw[:1].isalpha() and raw[:1].islower():
        score = min(score, 3.0)
    return max(0.0, min(score, 10.0))


def _starts_mid_thought(sentence: dict) -> bool:
    words = re.findall(r"[\w-]+", _opening_text(sentence).lower())
    return bool(words) and words[0] in _FRAGMENT_START


WEAK_OPENING = 4.5        # ниже этого ищем начало получше
SEARCH_BACK = 5           # на сколько фраз заглядываем НАЗАД
SEARCH_FORWARD = 8        # и на сколько вперёд


def _fix_start(sentences: list[dict], i: int, j: int,
               min_sec: float, max_sec: float) -> tuple[int, int]:
    """Ищет лучшее начало клипа. Возвращает (начало, конец).

    Если первая фраза вялая — перечисление или середина мысли, — ищем среди
    соседних фразу, с которой ролик зазвучит, и заново подгоняем конец.

    Искать надо В ОБЕ СТОРОНЫ. Раньше код шёл только вперёд, и оба вялых
    ролика он не чинил: они состоят из трёх фраз, идти вперёд некуда — после
    сдвига оставалось 16 и 12 секунд при минимуме 25. Сильное начало лежало
    ПЕРЕД тем, что выбрала нейросеть: «У меня на вулкан вообще большие планы».

    Три ограничения не дают лекарству стать хуже болезни:
      * новое начало обязано само пройти порог, иначе меняем шило на мыло;
      * длительность после сдвига строго в рамках, без поблажек;
      * клип должен наполовину совпасть со старым — иначе это уже другой ролик.
    При равной оценке побеждает меньший сдвиг: рядом с кульминацией стрима есть
    вторая такая же сильная фраза, и без этого правила ролик уезжал с неё.
    """
    current = _opening_score(sentences[i])
    if current >= WEAK_OPENING:
        return i, j

    length = j - i + 1
    found = []
    for k in range(max(i - SEARCH_BACK, 0),
                   min(i + SEARCH_FORWARD, len(sentences) - 1) + 1):
        if k == i:
            continue
        score = _opening_score(sentences[k])
        if score < WEAK_OPENING or score <= current:
            continue
        end = _fit_end(sentences, k, max(j, k), min_sec, max_sec)
        if end is None:
            continue
        duration = sentences[end]["end"] - sentences[k]["start"]
        if not (min_sec <= duration <= max_sec):
            continue
        overlap = min(end, j) - max(k, i) + 1
        if overlap < length * 0.5:
            continue
        found.append((score, k, end))
    if not found:
        return i, j
    # Из почти одинаковых по силе начал берём БЛИЖАЙШЕЕ. Рядом с кульминацией
    # стрима стоит вторая такая же сильная фраза, и без этого правила ролик
    # уезжал с «Я разочарован» на несколько фраз вперёд.
    top = max(s for s, _, _ in found)
    score, k, end = min(((s, k, e) for s, k, e in found if s >= top - 1.0),
                        key=lambda t: (abs(t[1] - i), -t[0]))
    return k, end


def _trim_filler(words: list[dict], start: float) -> float:
    """Сдвигает начало клипа за слова-паразиты. Возвращает новое начало.

    Если после срезания первым словом окажется союз, чистку отменяем:
    «Ну что 14 тысяч достаточно долго» — нормальное начало, а «что 14 тысяч
    достаточно долго» уже звучит как обрубок. Убирая одно, легко создать другое.
    """
    head = [w for w in words if start - 0.01 <= w["start"] < start + 2.5]
    cut = start
    removed = 0
    for n, w in enumerate(head):
        if removed >= MAX_FILLER_WORDS or w["end"] - start > MAX_FILLER_TRIM_SEC:
            break
        if not _FILLER_WORD.match(w["word"]):
            break
        following = head[n + 1]["word"].lower().strip(".,!?—-") if n + 1 < len(head) else ""
        if following in _FRAGMENT_START:
            break          # срезав это слово, оголим союз — оставляем как есть
        cut = w["end"]
        removed += 1
    return cut


def _piece_text(sentences: list[dict], i: int, j: int) -> str:
    return " ".join(sentences[k]["text"] for k in range(i, j + 1))


# ---------------------------------------------------------------- разнообразие

# служебные слова: встречаются у любых тем и только мешают их различать
_STOPWORDS = {
    "автор", "который", "которая", "которое", "чтобы", "потому", "этот", "этого",
    "здесь", "очень", "может", "можно", "нужно", "будет", "было", "если", "когда",
    "рассказ", "говорит", "объясн", "показ", "делает", "своих", "своей", "также",
    "после", "перед", "около", "более", "менее", "самый", "самое", "самая",
}
_STEM = 5              # грубая нормализация окончаний: «планету» и «планета» → «плане»
_SAME_TOPIC = 0.34     # выше этого считаем, что клипы про одно и то же


def _topic_words(*texts: str) -> set[str]:
    """Ключевые слова темы: длинные слова без окончаний и без служебных."""
    words = set()
    for text in texts:
        for raw in re.findall(r"[\w-]+", (text or "").lower()):
            if len(raw) < 4 or raw in _STOPWORDS:
                continue
            stem = raw[:_STEM]
            if stem not in _STOPWORDS:
                words.add(stem)
    return words


def _similarity(a: set[str], b: set[str]) -> float:
    """Насколько две темы совпадают: 0 — про разное, 1 — про одно и то же."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _grouping_prompt(cands: list[dict]) -> str:
    listing = "\n".join(
        f"[{n}] {c.get('about') or c.get('hook', '')}"
        for n, c in enumerate(cands)
    )
    return f"""Ниже — моменты, найденные в одном видео. Каждый описан одной фразой.

Сгруппируй их по СЮЖЕТНЫМ ЛИНИЯМ: моменты об одном и том же событии, одной теме
или одной части истории должны попасть в одну группу. Моменты про разное —
в разные группы.

Не объединяй всё подряд: если события действительно разные, пусть будет
много маленьких групп. И не дроби одну историю на части: два взгляда
на одно событие — это одна группа.

Каждой группе дай короткое название по-русски.

Ответь строго JSON:
{{"groups":[{{"topic":"название линии","ids":[0,3]}},{{"topic":"другая линия","ids":[1]}}]}}

Моменты:
{listing}
"""


def _pick_by_storylines(model: str, cands: list[dict], max_count: int,
                        min_gap_sec: float) -> list[dict] | None:
    """Одна сюжетная линия — один ролик. Возвращает столько роликов, сколько линий.

    Число роликов не задаётся заранее: сколько в видео нашлось разных историй,
    столько и получится (но не больше max_count).
    """
    if len(cands) < 2:
        return None
    try:
        raw = _ollama_chat(model, _grouping_prompt(cands), num_predict=400)
        groups = json.loads(raw).get("groups", [])
    except Exception as exc:
        print(f"  разбор сюжетных линий не удался ({type(exc).__name__})")
        return None

    seen: set[int] = set()
    best_of_group: list[dict] = []
    for g in groups:
        members = []
        for item in g.get("ids", []):
            try:
                idx = int(item)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(cands) and idx not in seen:
                seen.add(idx)
                members.append(cands[idx])
        if not members:
            continue
        # Из одной сюжетной линии берём НАЧАЛО сцены, а не её хвост, если
        # оценки почти равны. Судья шумит на пару баллов от прогона к прогону,
        # и без этого правила кульминация стрима через раз подменялась куском,
        # который идёт сразу за ней.
        top = max(c["score"] for c in members)
        winner = min((c for c in members if c["score"] >= top - 1.0),
                     key=lambda c: c["start"])
        winner["storyline"] = str(g.get("topic", ""))[:80]
        best_of_group.append(winner)

    # кандидаты, которых модель забыла разложить, считаем отдельными линиями
    for idx, c in enumerate(cands):
        if idx not in seen:
            c["storyline"] = c.get("about", "")[:80]
            best_of_group.append(c)

    if not best_of_group:
        return None

    best_of_group.sort(key=lambda c: -c["score"])
    chosen: list[dict] = []
    for c in best_of_group:
        if len(chosen) >= max_count:
            break
        if any(not (c["end"] + min_gap_sec < k["start"]
                    or c["start"] > k["end"] + min_gap_sec) for k in chosen):
            continue
        chosen.append(c)
    return chosen or None


MAX_TITLE_CHARS = 38      # длиннее не помещается в две строки титра
# Описание от разведки часто звучит как «Автор выражает разочарование в том, что…»
# или «Исследователь рассказывает про…». Опознаём не подлежащее — их бесконечно
# много, — а глагол речи: всё до него и есть служебная обёртка.
_NARRATOR_HEAD = re.compile(
    r"^\W*\w+\s+(?:выража|рассказ|объясн|показ|говор|сообща|отмеча|описыва|"
    r"делит|обсужда|размышля|комментиру|упомина)\w*\s*"
    r"(?:о\s+том,?|об\s+этом,?|о|об|про|что|как)?\s*[,:]?\s*", re.IGNORECASE)

# Эталоны стиля для запроса. Маленькая модель охотно списывает примеры дословно,
# поэтому они же служат чёрным списком: такое название в ролик не попадёт.
TITLE_EXAMPLES = (
    "Выживание на Глебе",
    "Путь к расколотой планете",
    "Расколотая планета — это разочарование",
    "Долгий путь в никуда",
)


def _is_copied_example(title: str) -> bool:
    """Модель списала пример из запроса вместо того, чтобы придумать своё."""
    normal = _topic_words(title)
    return any(_similarity(normal, _topic_words(sample)) >= 0.8
               for sample in TITLE_EXAMPLES)


def _naming_prompt(clips: list[dict]) -> str:
    examples = "\n".join(f"  «{x}»" for x in TITLE_EXAMPLES)
    listing = "\n\n".join(
        f"[{n}] сюжет: {c.get('storyline') or c.get('about', '')}\n"
        f"    речь: {c.get('text', '')[:420]}"
        for n, c in enumerate(clips)
    )
    return f"""Ты придумываешь названия для коротких видео.

Название должно быть ЗВУЧНЫМ: короткое, образное, передающее суть истории
и её настроение. Вот эталоны СТИЛЯ — делай такие же по духу, но про своё:
{examples}

Как думать: сначала пойми, ЧТО за история в куске — что человек делал,
чем это кончилось, что он почувствовал. Потом назови эту историю
одной короткой фразой, как называют главу книги.

Так НЕ надо:
  «Автор рассказывает о производстве пакетов» — это пересказ, а не название
  «Что 14 тысяч достаточно долго» — это первая фраза из речи
  «Решение отказаться от дальнейших действий» — канцелярит

Правила: от двух до пяти слов, не длиннее {MAX_TITLE_CHARS} символов.
Без слов «автор», «рассказывает», «объясняет», «показывает».
Можно назвать место, предмет или чувство, о которых идёт речь.
Не выдумывай того, чего в куске нет.

Ответь строго JSON:
{{"titles":[{{"id":0,"title":"Долгий путь в никуда"}}]}}

Куски:
{listing}
"""


def _make_titles(model: str, clips: list[dict]) -> None:
    """Даёт клипам звучные названия по сюжету. Правит clips на месте.

    Отдельный проход, потому что судья занят сравнением и выдаёт описания
    вида «Автор рассказывает о...». Название — другая задача: сначала понять
    историю, потом придумать ей имя.
    """
    if not clips:
        return
    try:
        raw = _ollama_chat(model, _naming_prompt(clips), num_predict=400)
        titles = json.loads(raw).get("titles", [])
    except Exception as exc:
        print(f"  названия не придумались ({type(exc).__name__}), беру рабочие")
        return
    for item in titles:
        try:
            idx = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        title = str(item.get("title", "")).strip().strip('"«»')
        if not (0 <= idx < len(clips)) or not title:
            continue
        if _is_copied_example(title):
            continue          # это пример из запроса, а не название ролика
        if len(title) > MAX_TITLE_CHARS:
            title = _fallback_title({"about": title})
        clips[idx]["hook"] = title[:1].upper() + title[1:]


def _drop_twin_titles(clips: list[dict]) -> list[dict]:
    """Убирает клипы с почти одинаковыми заголовками.

    Разбор по сюжетным линиям иногда разводит по разным группам то, что
    зритель прочитает как одно и то же: «Не могу поддерживать термоядерный
    синтез» и «Поддерживать термоядерный синтез» в одной подборке выглядят
    как ошибка. Это последняя проверка перед рендером.
    """
    kept: list[dict] = []
    for c in clips:
        title_words = _topic_words(c.get("hook", ""))
        twin = any(_similarity(title_words, _topic_words(k.get("hook", ""))) >= 0.65
                   for k in kept)
        if not twin:
            kept.append(c)
    return kept


def _diverse_prompt(best: dict, rest: list[dict], need: int) -> str:
    listing = "\n".join(
        f"[{n}] (оценка {c['score']:.0f}) {c.get('about') or c.get('hook', '')}"
        for n, c in enumerate(rest)
    )
    return f"""Собираем подборку роликов из одного видео. Один ролик уже выбран:

  «{best.get('about') or best.get('hook', '')}»

Ниже — остальные кандидаты с оценками силы. Выбери ещё {need} так, чтобы
подборка получилась ПРО РАЗНОЕ.

Правила:
- не бери то, что про тот же сюжет, что и уже выбранный ролик;
- не бери два кандидата про одно и то же между собой — из группы похожих
  оставь один, самый сильный;
- при прочих равных предпочитай высокую оценку.

Ответь строго JSON, номера в порядке от лучшего:
{{"picked":[0,4]}}

Кандидаты:
{listing}
"""


def _pick_diverse_by_model(model: str, cands: list[dict], count: int,
                           min_gap_sec: float) -> list[dict] | None:
    """Выбирает клипы про разные сюжеты. None, если не вышло.

    Сравнивать темы по совпадению слов бесполезно: один и тот же сюжет модель
    описывает каждый раз другими словами. А вот понять, что это одно и то же,
    она умеет.

    Самый сильный момент берётся всегда: без этого модель однажды собрала
    красивую разнообразную подборку, выкинув из неё кульминацию стрима.
    """
    if len(cands) <= count or count < 2:
        return None
    best = cands[0]                       # список уже отсортирован судьёй
    rest = cands[1:]
    try:
        raw = _ollama_chat(model, _diverse_prompt(best, rest, count - 1), num_predict=120)
        ids = json.loads(raw).get("picked", [])
    except Exception as exc:
        print(f"  выбор разных сюжетов не удался ({type(exc).__name__})")
        return None

    chosen = [best]
    for item in ids:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            continue
        if not (0 <= idx < len(rest)) or rest[idx] in chosen:
            continue
        c = rest[idx]
        if any(not (c["end"] + min_gap_sec < k["start"]
                    or c["start"] > k["end"] + min_gap_sec) for k in chosen):
            continue
        chosen.append(c)
        if len(chosen) >= count:
            break
    return chosen if len(chosen) > 1 else None


def _pick_diverse(cands: list[dict], count: int, min_gap_sec: float) -> list[dict]:
    """Отбирает лучшие клипы ПРО РАЗНОЕ.

    Раньше брались просто три самых сильных — и все три легко оказывались про
    один сюжет. Теперь каждый следующий клип обязан отличаться по теме от уже
    выбранных; если непохожих не хватает, требование постепенно смягчается,
    чтобы не остаться совсем без роликов.
    """
    for c in cands:
        c["topic"] = _topic_words(c.get("about", ""), c.get("hook", ""))

    chosen: list[dict] = []
    for limit in (_SAME_TOPIC, 0.5, 0.7, 1.1):   # постепенно смягчаем требование
        for c in cands:
            if len(chosen) >= count:
                break
            if c in chosen:
                continue
            too_close_in_time = any(
                not (c["end"] + min_gap_sec < k["start"] or c["start"] > k["end"] + min_gap_sec)
                for k in chosen)
            if too_close_in_time:
                continue
            same_topic = max((_similarity(c["topic"], k["topic"]) for k in chosen), default=0.0)
            if same_topic >= limit:
                continue
            c["topic_overlap"] = round(same_topic, 2)
            chosen.append(c)
        if len(chosen) >= count:
            break
    return chosen


# ---------------------------------------------------------------- разведка

def _scout(model: str, sentences: list[dict], min_sec: float,
           max_sec: float) -> list[dict]:
    """Проходит по всему видео окнами и собирает кандидатов."""
    found = []
    step = max(CHUNK_SENTENCES - CHUNK_OVERLAP, 1)
    for lo in range(0, len(sentences), step):
        hi = min(lo + CHUNK_SENTENCES, len(sentences))
        if hi - lo < 3:
            break
        try:
            raw = _ollama_chat(model, _scout_prompt(sentences, lo, hi, min_sec, max_sec),
                               num_predict=700)
            data = json.loads(raw)
        except Exception as exc:
            print(f"  окно {lo}-{hi}: пропущено ({type(exc).__name__})")
            continue
        for item in data.get("moments", []):
            try:
                i, j = int(item["start_id"]), int(item["end_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (lo <= i <= j < hi):
                continue
            found.append({"i": i, "j": j,
                          "strength": float(item.get("strength") or 5),
                          "about": str(item.get("about", ""))[:200]})
        if hi >= len(sentences):
            break
    return found


def _dedupe(cands: list[dict]) -> list[dict]:
    """Убирает настоящие дубли — куски, совпадающие больше чем наполовину.

    Раньше выбрасывался любой кандидат, хоть краем задевший другого. Из-за
    этого история «метеориты били в угол — поставил рельсовую пушку — вопросов
    больше нет» вылетала каждый раз: она перекрывалась с экскурсией по базе
    тремя фразами, и слабый кусок вытеснял сильный.
    """
    cands.sort(key=lambda c: -c["strength"])
    kept: list[dict] = []
    for c in cands:
        duplicate = False
        for k in kept:
            overlap = min(c["j"], k["j"]) - max(c["i"], k["i"]) + 1
            if overlap <= 0:
                continue
            shorter = min(c["j"] - c["i"] + 1, k["j"] - k["i"] + 1)
            if overlap >= shorter * 0.5:
                duplicate = True
                break
        if not duplicate:
            kept.append(c)
    return kept


# ---------------------------------------------------------------- основное

def pick_highlights_smart(transcript: dict, total_duration: float, count: int,
                          min_sec: float, max_sec: float, min_gap_sec: float,
                          model: str, signals=None, cfg: dict | None = None,
                          exact_count: bool = False) -> tuple[list[dict], str]:
    """Возвращает (клипы, движок). Клип: {'pieces': [[a,b],...], 'hook', 'score'}."""
    av = cfg.get("av_signals", {}) if cfg else {}

    def fallback():
        clips = pick_highlights(transcript, total_duration, count,
                                min_sec, max_sec, min_gap_sec)
        for c in clips:
            c.setdefault("pieces", [[c["start"], c["end"]]])
        return clips, "heuristic"

    sentences = _split_sentences(transcript.get("segments", []))
    if len(sentences) < 3 or not ollama_available(model):
        return fallback()

    words = [w for s in transcript.get("segments", []) for w in s.get("words", [])]

    try:
        raw_cands = _scout(model, sentences, min_sec, max_sec)
        if not raw_cands:
            print("  нейросеть не нашла сильных моментов, использую эвристику")
            return fallback()

        # подгоняем границы кодом — модель свои же правила нарушает
        prepared = []
        for c in _dedupe(raw_cands):
            j = _fit_end(sentences, c["i"], c["j"], min_sec, max_sec)
            if j is None:
                continue
            begin, j = _fix_start(sentences, c["i"], j, min_sec, max_sec)
            start = _trim_filler(words, sentences[begin]["start"])
            end = min(sentences[j]["end"], total_duration)
            if end - start < min_sec * 0.6:
                continue
            prepared.append({
                "i": begin, "j": j, "start": round(start, 2), "end": round(end, 2),
                "duration": end - start, "strength": c["strength"],
                "text": _piece_text(sentences, begin, j),
                "about": c.get("about", ""),
                "closed": bool(sentences[j].get("strong")),
                "opening": _opening_score(sentences[begin]),
            })
        if not prepared:
            return fallback()

        # Вялое начало — минус к силе кандидата: если ролик целиком стоит
        # на перечислении, сдвигать начало некуда, остаётся не брать его вовсе.
        # Поправку храним отдельно и возвращаем ПОСЛЕ судьи: он переписывает
        # оценку целиком, и раньше этот штраф пропадал впустую.
        # Только штраф, без награды: награда за яркое начало однажды увела
        # кульминацию стрима на соседний кусок, который начинается её же
        # последней фразой «короче полная шляпа».
        for c in prepared:
            c["opening_adjust"] = min(0.0, (c["opening"] - 5.0) * 0.5)
            c["strength"] += c["opening_adjust"]

        # штрафы и бонусы по картинке и звуку
        if signals is not None and getattr(signals, "ok", False) and av.get("enabled", True):
            for c in prepared:
                frozen = signals.freeze_share(c["start"], c["end"])
                dark = signals.dark_share(c["start"], c["end"])
                energy = signals.energy(c["start"], c["end"])
                # Поправку храним отдельно: судья перезапишет оценку, и штраф
                # за застывший экран иначе бы потерялся.
                # Величина намеренно скромная: оценки идут по десятибалльной
                # шкале, и слишком крупный штраф однажды выбросил кульминацию стрима
                # только за то, что в кадре была статичная карта.
                c["av_adjust"] = (
                    - frozen * float(av.get("freeze_penalty", 2.0))
                    - dark * float(av.get("dark_penalty", 1.0))
                    + max(energy, 0) * float(av.get("energy_bonus", 0.10))
                )
                c["strength"] += c["av_adjust"]
                c["frozen"] = round(frozen, 2)
            prepared.sort(key=lambda c: -c["strength"])

        # судья сравнивает выживших между собой и придумывает заголовки
        judged = _judge(model, prepared[:12])
        judged = _drop_scene_tails(judged, min_gap_sec)
        # разбираем кандидатов по сюжетным линиям: одна линия — один ролик
        picked = _pick_by_storylines(model, judged, count, min_gap_sec)
        if not picked:
            picked = (_pick_diverse_by_model(model, judged, count, min_gap_sec)
                      or _pick_diverse(judged, count, min_gap_sec))
        # если попросили ровно N роликов, а линий нашлось меньше — добираем
        if exact_count and len(picked) < count:
            for c in judged:
                if len(picked) >= count:
                    break
                if c in picked:
                    continue
                if any(not (c["end"] + min_gap_sec < k["start"]
                            or c["start"] > k["end"] + min_gap_sec) for k in picked):
                    continue
                picked.append(c)

        _make_titles(model, picked)          # звучные названия по сюжету
        picked = _drop_twin_titles(picked)

        clips = []
        for c in picked:
            clips.append({
                "pieces": [[c["start"], c["end"]]],
                "start": c["start"], "end": c["end"],   # для обратной совместимости
                "hook": c["hook"],
                "about": c.get("about", ""),
                "storyline": c.get("storyline", ""),
                "score": round(c["score"], 2),
            })
        if clips:
            for c in clips:
                print(f"  сюжет: {c.get('storyline') or c.get('about', '')[:60]}")
            return clips, "ollama"
    except Exception as exc:
        print(f"  Ollama не ответила ({type(exc).__name__}), использую эвристику")
    return fallback()


def _fallback_title(c: dict) -> str:
    """Заголовок, когда судья не сработал.

    Сырую расшифровку не берём никогда: она попадает титром прямо в кадр
    и выглядит как обрывок бормотания. Обрезаем по границе слова, а не
    по счётчику символов — иначе титр обрывается на середине слова.
    """
    text = (c.get("about") or c.get("text") or "").strip()
    if not text:
        return "Момент"
    # описания от разведки часто начинаются с «Автор рассказывает о...» —
    # в заголовок такое пускать нельзя, срезаем служебное начало
    text = _NARRATOR_HEAD.sub("", text).strip()
    text = text.rstrip(" .").strip()
    if not text:
        return "Момент"
    # длинное описание сокращаем до первой части — она обычно и есть суть
    for sep in (", но ", ", а ", ", и ", ", "):
        head = text.split(sep)[0]
        if 20 <= len(head) <= 55:
            text = head
            break
    if len(text) > 55:
        words = text[:55].split()
        text = " ".join(words[:-1]) if len(words) > 1 else words[0]
    # предлог или союз в конце выдаёт обрубок: «...отказаться из-за»
    tail = {"из-за", "для", "при", "над", "под", "перед", "через", "около", "без",
            "про", "что", "чтобы", "потому", "как", "когда", "если", "и", "а",
            "но", "в", "на", "с", "к", "по", "от", "до", "у", "о", "об", "за"}
    parts = text.split()
    while len(parts) > 2 and parts[-1].lower().strip(",") in tail:
        parts.pop()
    text = " ".join(parts)
    return text[:1].upper() + text[1:]


def _drop_scene_tails(cands: list[dict], min_gap_sec: float) -> list[dict]:
    """Два куска, стоящие вплотную, — это одна сцена, и в подборку всё равно
    попадёт только один из них. При почти равных оценках оставляем НАЧАЛО
    сцены, а не её хвост.

    Иначе выходило так: судья на одном прогоне ставил кульминации 9, а куску
    сразу за ней — 10, и зритель получал ролик, начинающийся с последних слов
    той же сцены. Оценки судьи гуляют на пару баллов, начало сцены — нет.
    """
    drop = set()
    for a in cands:
        for b in cands:
            if a is b or a["start"] >= b["start"]:
                continue
            apart = a["end"] + min_gap_sec < b["start"] or a["start"] > b["end"] + min_gap_sec
            if not apart and b["score"] - a["score"] <= 1.0:
                drop.add(id(b))
    return [c for c in cands if id(c) not in drop]


def _judge(model: str, cands: list[dict]) -> list[dict]:
    """Ранжирует кандидатов по силе. Заголовки даёт _make_titles.

    Раньше судья заодно придумывал названия, и однажды списал пример прямо
    из промпта: ролик про Factorio получил заголовок «Не пустил жильца
    из-за подделки». Названия теперь делает только тот, чья это работа.
    """
    for c in cands:
        c.setdefault("hook", _fallback_title(c))
        c.setdefault("score", c["strength"])
    if not cands:
        return cands
    try:
        raw = _ollama_chat(model, _judge_prompt(cands), num_predict=1500)
        ranking = json.loads(raw).get("ranking", [])
    except Exception as exc:
        print(f"  судья не ответил ({type(exc).__name__}), беру порядок разведки")
        return sorted(cands, key=lambda c: -c["strength"])

    ordered = []
    for item in ranking:
        try:
            idx = int(item["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= idx < len(cands)) or cands[idx] in ordered:
            continue
        c = cands[idx]
        c["score"] = float(item.get("score") or c["strength"])
        # незакрытый финал — минус к оценке, такой клип берём в последнюю очередь
        if not c.get("closed"):
            c["score"] -= 1.5
        # возвращаем поправки, которых судья не видит: картинка со звуком
        # и вялое начало (судья читает весь текст разом и на первую фразу
        # внимания не обращает — он дважды ставил перечислению десятку)
        c["score"] += c.get("av_adjust", 0.0)
        c["score"] += c.get("opening_adjust", 0.0)
        ordered.append(c)
    for c in cands:
        if c not in ordered:
            ordered.append(c)
    ordered.sort(key=lambda c: -c["score"])
    return ordered
