#!/usr/bin/env python3
"""Линтер карточки постановки.

Две разные вещи, которые нельзя смешивать:

  ОШИБКИ     — структура. Поле отсутствует, пусто или содержит заглушку; у условия
               цели, «я не хочу», условия или решения владельца, риска нет ссылки
               на пункт приёмки [Пn] или пометки [не проверяется: причина]; ссылка
               ведёт на несуществующий пункт; у опровергающего шага нет строк
               «Вероятный исход» и «Провал». Проверяется детерминированно.
               Рядом с номером — цитата из этого пункта: [П1 «слова пункта»];
               скрипт ищет её в тексте пункта, название пункта и шаблонные
               слова цитатой не считает. Подходит ли цитата по смыслу, судит критик.
               Ответ владельца из виджета стоит в условии блоком {ответ: «вариант» —
               «его описание»}; числа из блока (часы, даты, суммы) обязаны быть в
               пункте, на который ссылается условие (1.15.0). Если была прожарка,
               а таких блоков нет, — ошибка.
  ПОДОЗРЕНИЯ — смысл, угаданный по словам. Артефакт вместо изменения, абстрактный
               владелец, число без замера, слово полноты («все», «нет», «не найдено»,
               «исправлено») без «N из M» или охвата. Скрипт словарём смысла не
               понимает, поэтому выдаёт их как поводы посмотреть, а не как запрет.
               Судит критик.
  ПРЕДУПРЕЖДЕНИЯ — третий список, на код возврата не влияет: у «Ожидаемого эффекта»
               нет срока проверки (ни даты, ни «проверяем <событие>») или срок
               относительный без календарной даты; нет строки «**Номер карточки:**
               OC-ГГГГММДД-ЧЧММ» или номер другого вида.

Карточка формата до 1.13.0 (без нумерации приёмки) получает одну ошибку о формате,
остальные проверки на ней прежние.

Коды возврата: 0 чисто, 1 есть ошибки структуры, 2 ошибка запуска.
Подозрения сами по себе кода 1 не дают; их печатает отдельным блоком.

  outcome_card_check.py card.md            проверить файл
  outcome_card_check.py card.md --strict   подозрения тоже дают код 1
  outcome_card_check.py --self-test        прогнать встроенные примеры
"""

from __future__ import annotations

from pathlib import Path
import argparse
import re
import sys


REQUIRED = [
    ("Было", r"\*\*Было:\*\*"),
    ("Владелец результата", r"\*\*Владелец результата:\*\*"),
    ("Когда", r"\*\*Когда:\*\*"),
    ("Сейчас (A)", r"\*\*Сейчас \(A\):\*\*"),
    ("Я хочу (G)", r"\*\*Я хочу \(G\):\*\*"),
    ("Я не хочу", r"\*\*Я не хочу:\*\*"),
    ("Условия и решения владельца", r"\*\*Условия и решения владельца:\*\*"),
    ("Сдаётся и принимается", r"\*\*Сдаётся и принимается:\*\*"),
    ("Ожидаемый эффект", r"\*\*Ожидаемый эффект:\*\*"),
    ("Gaps", r"\*\*Gaps:\*\*"),
    ("Неизвестные", r"\*\*Неизвестные"),
    ("Риски", r"\*\*Риски:\*\*"),
    ("Критическая цепочка", r"\*\*Критическая цепочка:\*\*"),
    ("Минимальный опровергающий шаг", r"\*\*Минимальный опровергающий шаг:\*\*"),
    ("Evidence", r"\*\*Evidence:\*\*"),
    ("Negative gates", r"\*\*Negative gates:\*\*"),
    ("Статус карточки", r"\*\*Статус карточки:\*\*"),
]




OPTIONAL_LABELS = [
    "Уровень", "Траектории", "Downstream", "Прожарка", "Проверка критиком", "Домены и подходы",
]
KNOWN_LABELS = [
    "Было", "Владелец результата", "Когда", "Сейчас (A)", "Я хочу (G)", "Я не хочу",
    "Условия и решения владельца", "Сдаётся и принимается", "Ожидаемый эффект", "Gaps",
    "Неизвестные", "Риски",
    "Критическая цепочка", "Минимальный опровергающий шаг", "Evidence",
    "Negative gates", "Статус карточки",
] + OPTIONAL_LABELS



LABEL_VARIANTS = KNOWN_LABELS + [
    "Неизвестные (догадками не заполнять)",
    "Downstream (отдельные задачи)",
]
LABEL_RE = re.compile(
    r"^\*\*(" + "|".join(re.escape(x) for x in sorted(LABEL_VARIANTS, key=len, reverse=True))
    + r"):?\*\*", re.M)

PLACEHOLDER_RE = re.compile(r"^(?:<[^>\n]*>|TBD|TODO|\.\.\.|—|-)$", re.IGNORECASE)

BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s*")


def strip_bullet(line: str) -> str:
    return BULLET_RE.sub("", line).strip().rstrip(".;,")


def is_placeholder(line: str) -> bool:
    """Строка не несёт содержания.

    Нормализация снимает маркер списка и хвостовую пунктуацию, поэтому «...» и «-»
    после неё становятся пустыми. Пустота регулярным выражением не ловится, и такие
    заглушки проходили проверку — отсюда явная проверка на пустую строку.
    """
    core = strip_bullet(line)
    return (not core) or bool(PLACEHOLDER_RE.match(core))




ABSTRACT_OWNER_WHOLE = ["все", "любой", "рынок", "команда", "бизнес", "аудитория"]

ABSTRACT_OWNER_STEM = ["пользовател", "стейкхолдер", "клиенты"]
ARTIFACT_G = ["собрать", "собрана", "сделать", "сделан", "подключить", "подключена",
              "написать", "написан", "создать", "создан", "выгрузить", "выгружен",
              "прогнать", "запустить", "презентация", "лендинг", "файл", "скрипт"]
ABSTRACT_G = ["улучшить", "улучшение", "повысить эффективност", "оптимизировать",
              "максимум пользы", "минимум усилий", "разобраться", "проработать",
              "усилить", "прокачать"]

NUMBER_RE = re.compile(r"[+\-−]?\d[\d\s.,]*\s*(%|п\.п\.|₽|руб|час|дн|раз)", re.IGNORECASE)
BASELINE_RE = re.compile(r"baseline|базов|замер|измерен|гипотез|порог|пре-окно|контрол|MDE|мощност",
                         re.IGNORECASE)




STATUS_READY_RE = re.compile(r"^готово к исполнению\.?$", re.IGNORECASE)
STATUS_BLOCKED_RE = re.compile(r"^заблокирован[оа]?\s*[:—-]\s*\S+", re.IGNORECASE | re.S)

STATUS_TEMPLATE_RE = re.compile(r"\|")




EFFECT_OK_RE = re.compile(r"отвеча|ответствен", re.IGNORECASE)
EFFECT_WHEN_RE = re.compile(r"провер|срок|через|к \d|до \d|\d{1,2}\.\d{2}", re.IGNORECASE)

OTHER_ROLE_RE = re.compile(
    r"чтобы\s+(продавц|сейлз|кассир|клиент|команда|руководител|маркетолог|подрядчик|"
    r"арендатор|партн[её]р|сотрудник)", re.IGNORECASE)





TRACE_FIELDS = ["Я хочу (G)", "Я не хочу", "Условия и решения владельца", "Риски"]
TRACE_MAY_BE_NONE = {"Условия и решения владельца", "Риски"}
ACC_ITEM_RE = re.compile(r"^\s*(?:[-*+•]\s+)?(?:\*\*)?П(\d+)(?:\*\*)?\s*[.):—–-]", re.M)
BRACKET_RE = re.compile(r"\[([^\[\]]*)\]")
NOCHECK_RE = re.compile(r"\[\s*не проверяется\s*:?\s*([^\[\]]*)\]", re.IGNORECASE)
NONE_FIELD_RE = re.compile(r"^нет(?:\s*[:.—–-].*)?$", re.IGNORECASE)

ITEM_BULLET_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")









QUOTED_FIELDS = set(TRACE_FIELDS)
REF_RE = re.compile(r"(?<![а-яёa-zA-ZА-ЯЁ])П\s*(\d+)")
MIN_QUOTE_WORDS = 2
TEMPLATE_WORDS = {"принимается", "если", "сдается", "сдаётся", "пункт", "пункта", "раздел", "есть", "что",
                  "это", "и", "или", "а", "в", "во", "на", "по", "с", "со", "к", "у", "о", "для", "из",
                  "до", "не", "от", "за", "при"}
NAME_MAX_WORDS = 8






ANSWER_FIELD = "Условия и решения владельца"
ANSWER_OPEN_RE = re.compile(r"\{\s*ответ\s*:", re.IGNORECASE)
NUM_RE = re.compile(r"\d+(?:[.,:]\d+)*(?:\s*[‑–—-]\s*\d+(?:[.,:]\d+)*)?")
NOT_COND_RE = re.compile(r"^\s*не\s+условие\s*:\s*(.*)$", re.IGNORECASE | re.S)
GRILL_ROUNDS_RE = re.compile(r"(\d+)\s*раунд", re.IGNORECASE)


FALSIFIER_SLOTS = ["Вероятный исход", "Провал"]




COMPLETENESS_FIELDS = ["Сейчас (A)", "Gaps", "Проверка критиком"]
_NB = r"(?<![а-яёa-zA-ZА-ЯЁ])"
_NA = r"(?![а-яёa-zA-ZА-ЯЁ])"
COMPLETENESS_RE = re.compile(
    _NB + r"(вс[её](?!\s+(?:ещё|еще|равно|же)" + _NA + r")|всех|всем|всеми|"
    r"везде|нигде|(?<!или\s)(?<!либо\s)нет|ни одного|ни одной|не найден[аоы]?|"
    r"не нашл[аио]сь|не нашёл|не нашли|не обнаружен[аоы]?|исправлен[аоы]?)" + _NA,
    re.IGNORECASE)


SET_WORDS = {"все", "всё", "всех", "всем", "всеми"}

SET_SIZE_RE = re.compile(
    r"\s+(?:\d+|дв[аеу]х?|две|тр[её]х|три|четыр[её]х|четыре|пят[иь]|шест[иь]|сем[иь]|"
    r"восьм[иь]|восемь|девят[иь]|десят[иь]|обе|оба|обоих|обеих)" + _NA, re.IGNORECASE)
NOFM_RE = re.compile(r"\d+\s+из\s+\d+")

SCOPE_RE = re.compile(r"охват|поиск|искал", re.IGNORECASE)
LOCATOR_RE = re.compile(r"\[E\d+|`[^`\n]+`|~/|(?<![\w])/[\w.-]+/")

COMMAND_RESULT_RE = re.compile(r"`[^`\n]+`[^\n]*?(?:→|->)")
QUOTE_RE = re.compile(r"«[^«»]*»|“[^“”]*”|\"[^\"\n]*\"")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[«(\[A-ZА-ЯЁ])")


def parse_fields(text: str) -> dict[str, str]:
    """Разрезать карточку по жирным меткам.

    Поле кончается там, где начинается следующая метка, а не на пустой строке:
    иначе пустое поле молча забирает содержимое соседнего.
    """
    bounds = [(m.start(), m.end()) for m in LABEL_RE.finditer(text)]
    fields: dict[str, str] = {}
    for i, (start, end) in enumerate(bounds):
        label = text[start:end].strip("*: ").strip()
        stop = bounds[i + 1][0] if i + 1 < len(bounds) else len(text)
        fields[label] = text[end:stop].strip()
    return fields


def find_field(fields: dict[str, str], pattern: str) -> tuple[str, str] | None:
    """Найти поле по метке. Сравнение текстовое: скобки в «Сейчас (A)» — это
    скобки, а не группа регулярного выражения."""
    bare = (pattern.replace(r"\*\*", "").replace(r"\(", "(").replace(r"\)", ")")
            .rstrip(":").strip())
    for label, body in fields.items():
        if label.startswith(bare):
            return label, body
    return None


def word_hit(haystack: str, stems: list[str], whole: list[str] | None = None) -> str | None:
    """Совпадение по началу слова, а не по подстроке.

    `whole` требует, чтобы после слова не шла буква: иначе «все» находится
    внутри «Всеволод» и конкретный владелец объявляется абстракцией.
    """
    for n in whole or []:
        if re.search(r"(?<![а-яёa-zA-ZА-ЯЁ])" + re.escape(n) + r"(?![а-яёa-zA-ZА-ЯЁ])",
                     haystack, re.IGNORECASE):
            return n
    for n in stems:
        if re.search(r"(?<![а-яёa-zA-ZА-ЯЁ])" + re.escape(n), haystack, re.IGNORECASE):
            return n
    return None


def shorten(s: str, n: int = 90) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def field_items(body: str) -> list[str]:
    """Пункты поля: строка списка вместе с продолжением либо абзац."""
    items: list[str] = []
    cur: str | None = None
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            if cur is not None:
                items.append(cur)
                cur = None
        elif ITEM_BULLET_RE.match(raw):
            if cur is not None:
                items.append(cur)
            cur = ITEM_BULLET_RE.sub("", raw, count=1).strip()
        else:
            cur = line if cur is None else cur + " " + line
    if cur is not None:
        items.append(cur)
    return items


def split_top_level(s: str, sep: str = ";") -> list[str]:
    """Разрезать по `sep` вне скобок и «ёлочек»: «a; b», [П1; П2] и {ответ: …; …} не режутся."""
    parts, start, depth = [], 0, 0
    i = 0
    while i < len(s):
        ch = s[i]
        if ch in ('"', "«"):
            quote = _balanced_quote(s, i)
            if quote:
                i = quote[1]
                continue
        if ch in "([«{":
            depth += 1
        elif ch in ")]»}" and depth:
            depth -= 1
        if ch == sep and depth == 0:
            parts.append(s[start:i])
            start = i + 1
        i += 1
    parts.append(s[start:])
    return [p.strip() for p in parts if p.strip()]


def trace_items(body: str) -> list[str]:
    """Отдельные условия поля: пункты списка, абзацы и их части через «;».
    Вводная строка перед списком («Из запроса:») условием не считается."""
    out: list[str] = []
    for item in field_items(body):
        if item.rstrip().rstrip("*_").rstrip().endswith(":"):
            continue
        out.extend(split_top_level(item))
    return out


def _balanced_quote(s: str, i: int) -> tuple[str, int] | None:
    """Read a straight-quoted string or a possibly nested guillemet quotation."""
    if s[i] == '"':
        end = s.find('"', i + 1)
        return (s[i + 1:end], end + 1) if end >= 0 else None
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "«":
            depth += 1
        elif s[j] == "»":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return None


def bracket_refs(b: str) -> list[tuple[int, str | None]]:
    """Ссылки внутри скобок: (номер пункта, цитата или None). Номера внутри цитаты не ссылки."""
    out: list[tuple[int, str | None]] = []
    pos = 0
    while True:
        m = REF_RE.search(b, pos)
        if not m:
            return out
        j = m.end()
        while j < len(b) and b[j] in " \t":
            j += 1
        q = None
        if j < len(b) and b[j] in ('"', "«"):
            bq = _balanced_quote(b, j)
            if bq:
                q, j = bq
        out.append((int(m.group(1)), q))
        pos = j


def acc_refs(s: str) -> list[int]:
    return [n for b in BRACKET_RE.findall(s) for n, _ in bracket_refs(b)]


def slot_value(body: str, name: str) -> str | None:
    m = re.search(r"^\s*(?:[-*+•]\s+)?(?:\*\*)?" + re.escape(name)
                  + r"(?:\*\*)?\s*:(?:\*\*)?[ \t]*(.*)$", body, re.M | re.IGNORECASE)
    return None if m is None else m.group(1).strip()


def norm_text(s: str) -> str:
    s = s.lower().replace("ё", "е").replace("\u00a0", " ")
    s = re.sub(r"[*_`«»“”„\"']", "", s)
    s = re.sub(r"[—–‑-]", "-", s)
    return re.sub(r"\s+", " ", s).strip()


NAME_STOP_RE = re.compile(r"(?<![а-яa-z])(?:принимается|сдается|если)(?![а-яa-z])")


def acc_item_texts(body: str) -> dict[int, tuple[str, str]]:
    """Пункт приёмки «П<n>. …» до следующего пункта: (весь текст, название), нормализованные.
    Название — короткий заголовок до «—» или «:», например «Путь гостя — принимается, если…»."""
    marks = list(ACC_ITEM_RE.finditer(body))
    out: dict[int, tuple[str, str]] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        raw = body[m.end():end].strip()
        head = re.split(r"\s+[—–-]\s+|:\s", raw, maxsplit=1)[0]
        hn = norm_text(head)

        name = hn if len(head.split()) <= NAME_MAX_WORDS and not NAME_STOP_RE.search(hn) else ""
        out.setdefault(int(m.group(1)), (norm_text(raw), name))
    return out


def quote_errors(name: str, item: str, acc_texts: dict[int, tuple[str, str]]) -> list[str]:
    """У каждой ссылки [Пn] — цитата, которая есть в пункте Пn и не сводится к его названию
    или шаблонным словам."""
    errs: list[str] = []
    for b in BRACKET_RE.findall(item):
        for n, q in bracket_refs(b):
            if n not in acc_texts:
                continue
            full, title = acc_texts[n]
            if q is None:
                errs.append(f"«{name}»: у ссылки П{n} нет цитаты из этого пункта — нужно "
                            f"[П{n} «слова пункта, которыми он это проверяет»] — «{shorten(item)}»")
                continue
            qn = norm_text(q).strip(" .,;:")

            words = [w for w in (t.strip(".,;:()!?") for t in qn.split()) if re.search(r"[0-9a-zа-я]", w)]

            title_words = {t.strip(".,;:()!?") for t in title.split()}
            own = [w for w in words if w not in TEMPLATE_WORDS and w not in title_words]
            if len(words) < MIN_QUOTE_WORDS:
                errs.append(f"«{name}»: цитата из П{n} короче двух слов — «{shorten(item)}»")
            elif qn not in full:
                errs.append(f"«{name}»: цитаты «{shorten(q, 60)}» нет в пункте П{n} — пункт "
                            f"этого не проверяет или цитата неточная — «{shorten(item)}»")
            elif sum(w not in TEMPLATE_WORDS for w in words) < MIN_QUOTE_WORDS:
                errs.append(f"«{name}»: цитата из П{n} состоит из шаблонных слов — процитируй "
                            f"признак, которым пункт это проверяет — «{shorten(item)}»")
            elif title and (qn in title or len(own) < MIN_QUOTE_WORDS):
                errs.append(f"«{name}»: цитата из П{n} — его название, а не признак приёмки — "
                            f"«{shorten(item)}»")
    return errs


def answer_blocks(item: str) -> list[str]:
    """Содержимое блоков {ответ: …} условия; «»-цитаты внутри могут содержать любые знаки."""
    out: list[str] = []
    pos = 0
    while True:
        m = ANSWER_OPEN_RE.search(item, pos)
        if not m:
            return out
        j = m.end()
        while j < len(item):
            ch = item[j]
            if ch in ('"', "«"):
                quote = _balanced_quote(item, j)
                if not quote:
                    j = len(item)
                    break
                j = quote[1]
                continue
            if ch == "}":
                break
            j += 1
        out.append(item[m.end():j])
        pos = j + 1


def top_quotes(s: str) -> list[str]:
    """Цитаты верхнего уровня; вложенные «…» остаются внутри своей цитаты."""
    out: list[str] = []
    i = 0
    while True:
        while i < len(s) and s[i] not in ('"', "«"):
            i += 1
        if i == len(s):
            return out
        bq = _balanced_quote(s, i)
        if not bq:
            return out
        out.append(bq[0])
        i = bq[1]


def _num_norm(tok: str) -> str:
    tok = re.sub(r"\s*[‑–—-]\s*", "-", tok)

    return re.sub(r"(?<!\d)0+(?=\d)", "", tok)


DATE_TIME_RE = re.compile(r"^(\d{1,2})[.:]\d{1,2}(?:\.\d{2,4})?$")

DATE_TIME_AFTER = re.compile(r"^\s*(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|"
                             r"декабр|час|ч\b|утра|вечера|дня)", re.IGNORECASE)
DATE_TIME_BEFORE = re.compile(r"(?:^|\s)(?:с|со|в|во|до|к|после)\s+$", re.IGNORECASE)


def num_tokens(s: str) -> set[str]:
    """Числа текста вместе с концами диапазонов: «17–18» даёт 17-18, 17 и 18."""
    out: set[str] = set()
    for m in NUM_RE.finditer(s):
        t = _num_norm(m.group(0))
        out.add(t)
        if "-" in t:
            out.update(t.split("-"))
    return out


def date_heads(s: str) -> set[str]:
    """Первые части дат и времени: «09.07» даёт 9, «17:00–18:00» — 17 и 18. Дробь «3.5» тоже
    сюда попадёт, поэтому одиночное число ответа засчитывается по ней только рядом с месяцем
    или словами о времени."""
    out: set[str] = set()
    for m in NUM_RE.finditer(s):
        t = _num_norm(m.group(0))
        for part in [t] + (t.split("-") if "-" in t else []):
            dt = DATE_TIME_RE.match(part)
            if dt:
                out.add(dt.group(1))
    return out


def answer_errors(item: str, acc_texts: dict[int, tuple[str, str]]) -> list[str]:
    """Числа ответа владельца (вариант и его описание) есть в пунктах, на которые
    ссылается условие. Подходит ли пункт по смыслу, судит критик."""
    errs: list[str] = []
    name = ANSWER_FIELD
    blocks = answer_blocks(item)
    if not blocks:
        return errs
    refs = sorted({n for n in acc_refs(item) if n in acc_texts})
    if not refs:
        return errs
    in_items: set[str] = set()
    in_heads: set[str] = set()
    for n in refs:
        in_items |= num_tokens(acc_texts[n][0])
        in_heads |= date_heads(acc_texts[n][0])
    for content in blocks:
        quoted, excluded = [], set()
        for part in split_top_level(content):
            nc = NOT_COND_RE.match(part)
            if nc:
                body = nc.group(1)
                dash = re.search(r"\s+[—–-]\s+", body)
                nums, reason = (body[:dash.start()], body[dash.end():]) if dash else (body, "")
                if len(reason.strip()) < 3:
                    errs.append(f"«{name}»: в блоке ответа «не условие» без причины — "
                                f"нужно «не условие: 2 — оценка срока» — «{shorten(item)}»")
                excluded |= {_num_norm(m.group(0)) for m in NUM_RE.finditer(nums)}
            else:
                quoted.extend(top_quotes(part))
        if not quoted:
            errs.append(f"«{name}»: в блоке {{ответ: …}} нет дословного текста ответа в "
                        f"кавычках — «{shorten(item)}»")
            continue
        need = []
        for q in quoted:
            for m in NUM_RE.finditer(q):
                t = _num_norm(m.group(0))
                if t in excluded or t in need:
                    continue
                parts = t.split("-") if "-" in t else []
                if t in in_items or (parts and all(p in in_items for p in parts)):
                    continue
                if parts and all(p in in_items or p in in_heads for p in parts):
                    continue
                if t in in_heads and (DATE_TIME_AFTER.search(q[m.end():])
                                      or DATE_TIME_BEFORE.search(q[:m.start()])):
                    continue
                need.append(t)
        if need:
            nums = ", ".join(need)
            where = ", ".join(f"П{n}" for n in refs)
            errs.append(f"«{name}»: чисел {nums} из ответа владельца нет в {where} — "
                        f"решение не дошло до приёмки: допиши их в пункт или вынеси в блоке "
                        f"«не условие: <число> — причина» — «{shorten(item)}»")
    return errs


def trace_check(text: str, fields: dict[str, str]) -> tuple[bool, list[str]]:
    """Дошло ли каждое условие до приёмки. Возвращает (новый формат?, ошибки).

    Проверяется наличие номера, существование пункта и (для условий владельца и
    рисков) что цитата из пункта в нём есть. Подходит ли пункт по смыслу, судит критик.
    """
    errors: list[str] = []
    acc = find_field(fields, "Сдаётся и принимается")
    acc_nums = [int(n) for n in ACC_ITEM_RE.findall(acc[1] if acc else "")]
    fals = find_field(fields, "Минимальный опровергающий шаг")
    fals_body = fals[1] if fals else ""
    new_format = bool(
        acc_nums
        or find_field(fields, "Условия и решения владельца")
        or find_field(fields, "Риски")
        or any(slot_value(fals_body, s) is not None for s in FALSIFIER_SLOTS))
    if not new_format:
        errors.append(
            "карточка старого формата (до 1.13.0): нет нумерации приёмки П1, П2…, полей "
            "«Условия и решения владельца» и «Риски», строк «Вероятный исход» и «Провал» "
            "в опровергающем шаге; трассировка условий к приёмке не проверена")
        return False, errors

    if acc and not acc_nums:
        errors.append("пункты «Сдаётся и принимается» не пронумерованы: каждый пункт "
                      "начинается с «П<номер>.», на него ссылаются [П<номер>]")
    for n in sorted({n for n in acc_nums if acc_nums.count(n) > 1}):
        errors.append(f"пункт приёмки П{n} объявлен дважды — ссылка на него неоднозначна")
    known = set(acc_nums)
    acc_texts = acc_item_texts(acc[1]) if acc and acc_nums else {}
    if acc_nums:
        for n in sorted(set(acc_refs(text)) - known):
            errors.append(f"ссылка на несуществующий пункт приёмки П{n}")

    for name in TRACE_FIELDS:
        f = find_field(fields, name)
        if not f or not f[1].strip():
            continue
        body = f[1].strip()
        if name in TRACE_MAY_BE_NONE and "\n" not in body and NONE_FIELD_RE.match(body):
            continue
        for item in trace_items(body):
            if acc_refs(item):
                if name in QUOTED_FIELDS:
                    errors.extend(quote_errors(name, item, acc_texts))
                if name == ANSWER_FIELD:
                    errors.extend(answer_errors(item, acc_texts))
                continue
            nc = NOCHECK_RE.search(item)
            if nc:
                reason = nc.group(1).strip()
                if len(reason) < 3 or is_placeholder(reason):
                    errors.append(f"«{name}»: пометка «не проверяется» без причины — "
                                  f"«{shorten(item)}»")
                continue
            errors.append(f"«{name}»: нет ссылки на пункт приёмки [Пn] и нет пометки "
                          f"[не проверяется: причина] — «{shorten(item)}»")

    grill = find_field(fields, r"\*\*Прожарка:\*\*")
    rounds = GRILL_ROUNDS_RE.search(grill[1]) if grill and grill[1] else None
    cond = find_field(fields, r"\*\*Условия и решения владельца:\*\*")
    if rounds and int(rounds.group(1)) > 0 and not ANSWER_OPEN_RE.search(cond[1] if cond else ""):
        errors.append(f"«{ANSWER_FIELD}»: прожарка шла (раундов: {rounds.group(1)}), а ни одного ответа "
                      "владельца блоком {ответ: «вариант» — «описание»} в поле нет — "
                      "внеси каждый ответ дословно вместе с описанием выбранного варианта")

    if fals and fals_body.strip():
        for slot in FALSIFIER_SLOTS:
            val = slot_value(fals_body, slot)
            if val is None:
                errors.append(f"в «Минимальный опровергающий шаг» нет строки «{slot}: …»")
            elif not val or is_placeholder(val):
                errors.append(f"в «Минимальный опровергающий шаг» строка «{slot}» пуста "
                              "или содержит заглушку")
    return True, errors


def completeness_suspicions(fields: dict[str, str]) -> list[str]:
    """Слова полноты без «N из M» или охвата с местом (шаг 3, «в проверенном охвате»).

    Подозрение, а не ошибка: «нет» и «все» словарём не отличить от утверждения
    о диске, а жёсткий запрет толкал бы обходить слова вместо проверки набора.
    """
    out: list[str] = []
    for name in COMPLETENESS_FIELDS:
        f = find_field(fields, name)
        if not f or not f[1].strip():
            continue
        for item in field_items(f[1]):
            has_loc = bool(LOCATOR_RE.search(item))
            for sent in SENTENCE_RE.split(item):
                clean = QUOTE_RE.sub(lambda m: " " * len(m.group(0)), sent)
                hits = []
                for m in COMPLETENESS_RE.finditer(clean):
                    w = m.group(1)
                    if w.lower() in SET_WORDS and SET_SIZE_RE.match(clean, m.end()):
                        continue
                    hits.append(w)
                if not hits:
                    continue
                if (NOFM_RE.search(sent) or COMMAND_RESULT_RE.search(sent)
                        or (SCOPE_RE.search(sent) and has_loc)):
                    continue
                words = ", ".join(f"«{w}»" for w in dict.fromkeys(hits))
                out.append(f"ПОЛНОТА? «{name}»: {words} без «N из M» или «в проверенном "
                           f"охвате» с местом — «{shorten(sent)}»")
    return out


def check(text: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    suspicions: list[str] = []
    fields = parse_fields(text)
    new_format, trace_errors = trace_check(text, fields)

    for name, pattern in REQUIRED:
        found = find_field(fields, pattern)
        if found is None:

            if new_format or name not in TRACE_MAY_BE_NONE:
                errors.append(f"нет обязательного поля: {name}")
            continue
        body = found[1]
        lines = [l for l in body.splitlines() if l.strip()]
        if not body or not lines:
            errors.append(f"поле пусто: {name}")
        elif all(is_placeholder(l) for l in lines):
            errors.append(f"поле содержит только заглушку: {name}")

    status = find_field(fields, r"\*\*Статус карточки:\*\*")
    if status and status[1]:
        body = status[1].strip()
        if STATUS_TEMPLATE_RE.search(body.splitlines()[0]):
            errors.append("статус карточки: состояние не выбрано, осталась строка альтернатив")
        elif STATUS_BLOCKED_RE.match(body):

            reason = re.sub(r"^заблокирован[оа]?\s*[:—-]\s*", "", body,
                            flags=re.IGNORECASE).strip()
            if all(is_placeholder(l) for l in reason.splitlines() if l.strip()) or not reason:
                errors.append(
                    "статус карточки: причина блокировки не заполнена — "
                    "назови, чего не хватает и кто это даст")
        elif not STATUS_READY_RE.match(body):
            errors.append(
                "статус карточки: нужно ровно «готово к исполнению» либо "
                "«заблокировано: <чего не хватает>» с непустым объяснением")

    owner = find_field(fields, r"\*\*Владелец результата:\*\*")
    if owner and owner[1]:
        hit = word_hit(owner[1], ABSTRACT_OWNER_STEM, ABSTRACT_OWNER_WHOLE)
        if hit:
            suspicions.append(f"HF1? владелец похож на абстракцию (слово «{hit}»)")

    g = find_field(fields, r"\*\*Я хочу \(G\):\*\*")
    if g and g[1]:
        body = g[1]
        hit = word_hit(body, ARTIFACT_G)
        if hit:
            suspicions.append(f"HF2? в цели слово «{hit}» — похоже на артефакт или действие")
        hit = word_hit(body, ABSTRACT_G)
        if hit:
            suspicions.append(f"HF2? в цели абстракция «{hit}»")
        if NUMBER_RE.search(body):
            ev = find_field(fields, r"\*\*Evidence:\*\*")
            near = body + "\n" + (ev[1] if ev else "")
            if not BASELINE_RE.search(near):
                suspicions.append(
                    "HF3? в цели есть число, а замера нет ни в самой цели, ни в Evidence")


    if g and g[1]:
        eff = find_field(fields, r"\*\*Ожидаемый эффект:\*\*")
        eff_body = eff[1].strip() if eff else ""
        eff_named = bool(EFFECT_OK_RE.search(eff_body) and EFFECT_WHEN_RE.search(eff_body))


        if OTHER_ROLE_RE.search(g[1]):
            suspicions.append(
                "HF4? в цели действие другой роли — G должна остаться атомарной у владельца, "
                "а downstream перенесён в «Ожидаемый эффект» с ответственным и сроком"
                + ("" if eff_named else "; строка эффекта при этом не заполнена"))
        if eff_body and not eff_named:
            suspicions.append(
                "в строке «Ожидаемый эффект» не видно ответственного и срока проверки")

    unknown = find_field(fields, r"\*\*Неизвестные")
    if unknown and re.fullmatch(r"[-–—\s]*(нет|отсутствуют|none)\.?", unknown[1].strip(),
                                re.IGNORECASE):
        suspicions.append("неизвестных не заявлено ни одного — проверь, не заполнены ли догадками")

    errors.extend(trace_errors)
    suspicions.extend(completeness_suspicions(fields))
    return errors, suspicions


_MONTHS_GEN = ("января|февраля|марта|апреля|мая|июня|июля|августа|"
               "сентября|октября|ноября|декабря")
CALENDAR_DATE_RE = re.compile(
    r"(?<!\d)\d{1,2}\.\d{1,2}\.\d{4}(?!\d)"
    r"|(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)"
    r"|(?<!\d)\d{1,2}\s+(?:" + _MONTHS_GEN + r")(?![а-яё])",
    re.IGNORECASE)
_NUM_WORDS = (r"\d+|пару|пар[ау]|один|одну|два|две|три|четыре|пять|шесть|семь|восемь|"
              r"девять|десять|несколько")
RELATIVE_TERM_RE = re.compile(
    r"через\s+(?:(?:" + _NUM_WORDS + r")\s+)?(?:дн|день|недел|месяц|полгода|год|лет)"
    r"|следующ(?:ая|ей|ую)\s+недел[яеию]|следующ(?:ий|ем|его)\s+месяц",
    re.IGNORECASE)

CHECK_EVENT_RE = re.compile(r"(?<![а-яё])проверя(?:ем|ет|ют|ть)(?:ся)?\s+([^\n]*)", re.IGNORECASE)
EFFECT_LABEL_RE = re.compile(r"\*\*Ожидаемый эффект:\*\*")
ANY_LABEL_LINE_RE = re.compile(r"^\*\*[^*\n]+:\*\*", re.M)
CARD_NO_LINE_RE = re.compile(r"^\*\*Номер карточки:\*\*[ \t]*(.*)$", re.M)
CARD_NO_RE = re.compile(r"OC-\d{8}-\d{4}")


def effect_body(text: str) -> str | None:
    """Текст поля «Ожидаемый эффект» до следующей строки вида «**…:**»."""
    m = EFFECT_LABEL_RE.search(text)
    if not m:
        return None
    nxt = ANY_LABEL_LINE_RE.search(text, m.end())
    return text[m.end():nxt.start() if nxt else len(text)].strip()


def warnings(text: str) -> list[str]:
    out: list[str] = []
    body = effect_body(text)
    if body is None:

        if not any("Ожидаемый эффект" in e for e in check(text)[0]):
            out.append("в «Ожидаемом эффекте» нет срока проверки: ни календарной даты, ни события")
    else:
        has_date = bool(CALENDAR_DATE_RE.search(body))
        has_event = any(re.search(r"[0-9a-zа-яё]", re.sub(r"<[^>\n]*>", "", g), re.IGNORECASE)
                        for g in CHECK_EVENT_RE.findall(body))
        if not has_date:
            if RELATIVE_TERM_RE.search(body):
                rel = RELATIVE_TERM_RE.search(body).group(0)
                out.append(f"срок проверки эффекта относительный («{rel}») — "
                           "поставь календарную дату")
            elif not has_event:
                out.append("в «Ожидаемом эффекте» нет срока проверки: "
                           "ни календарной даты, ни события")
    m = CARD_NO_LINE_RE.search(text)
    if not m:
        out.append("нет номера карточки (строка «**Номер карточки:** OC-ГГГГММДД-ЧЧММ»)")
    elif not CARD_NO_RE.fullmatch(m.group(1).strip()):
        out.append(f"номер карточки неверного вида: «{shorten(m.group(1).strip(), 40)}» — "
                   "нужно OC-ГГГГММДД-ЧЧММ")
    return out


def self_test() -> int:
    sample = (Path(__file__).resolve().parents[1] / "references" / "example-card.md").read_text(encoding="utf-8")
    cases = [
        ("example", sample, False),
        ("missing owner", sample.replace("**Владелец результата:**", "**Кто:**"), True),
        ("wrong acceptance item", sample.replace('[П1 "выбирает экземпляр по шифру"]', '[П9 "выбирает экземпляр по шифру"]'), True),
        ("wrong quotation", sample.replace('[П1 "выбирает экземпляр по шифру"]', '[П1 "пропущенный текст"]'), True),
        ("missing failure case", sample.replace("- Провал:", "- Результат:"), True),
    ]
    for name, card, should_fail in cases:
        errors, _ = check(card)
        if bool(errors) != should_fail:
            print(f"{name}: unexpected result {errors}", file=sys.stderr)
            return 1
    print(f"Passed {len(cases)} structural checks.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Линтер карточки постановки")
    ap.add_argument("card", nargs="?", help="файл карточки .md")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="подозрения тоже дают ненулевой код возврата")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.card:
        ap.print_help()
        return 2
    try:
        text = open(args.card, encoding="utf-8").read()
    except OSError as exc:
        print(f"не читается: {exc}")
        return 2

    errors, suspicions = check(text)
    if errors:
        print("ОШИБКИ СТРУКТУРЫ:")
        for e in errors:
            print(f"  - {e}")
    if suspicions:
        print("ПОДОЗРЕНИЯ (решает критик, не скрипт):")
        for s in suspicions:
            print(f"  - {s}")
    if not errors and not suspicions:
        print("структура полна, подозрений нет; смысл скриптом не проверяется")
    elif not errors:
        print("структура полна; перечисленное выше — поводы посмотреть, не запрет")
    warns = warnings(text)
    if warns:
        print("ПРЕДУПРЕЖДЕНИЯ (на код возврата не влияют):")
        for w in warns:
            print(f"  - {w}")

    if errors:
        return 1
    return 1 if (args.strict and suspicions) else 0


if __name__ == "__main__":
    sys.exit(main())
