"""Парсер статьи: текст -> список ImageSlot.

Разметка из реального примера 8Blocks (TZ 8a):
строка-маркер начинается с «Рис.», за ним опц. заголовок на той же
строке, далее буллеты «•» до пустой строки или строки без буллета.
Буллеты вне маркера «Рис.» (например, варианты заголовков в начале
статьи) игнорируются — у них нет маркера сверху.
"""

from __future__ import annotations

import re

from .classifier import classify_type
from .models import ImageSlot, SlotType

MARKER = re.compile(
    # Опц. ведущий буллет (Word/Docs часто кладёт «- Рис. ...» вместо чистого «Рис.»)
    r"^\s*(?:[•\-\*·–—]\s+)?(?:Рис|Fig|Figure|Pic|Image)\.?\s*"
    r"(?:\[([^\]]+)\])?\s*(.*)$",
    re.IGNORECASE,
)
# Буллет в начале строки: • (docx/HTML), -, *, ·, –, — (Markdown/Docs).
# Обычные абзацы так не начинаются — границу блока не размывает.
BULLET_LINE = re.compile(r"^(?:[•\-\*·–—]|\d+[.\)])\s+(.*)$")

# Маркер встроенной картинки из .docx (поставлен в article_loader).
INLINE_IMAGE_LINE = re.compile(r"^\s*\[INLINE_IMAGE:(.+?)\]\s*$")

# Markdown-таблица: строка вида `| col | col |`.
TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
# Разделитель таблицы: `| --- | --- |` / `|---|:---:|---|`.
TABLE_SEPARATOR = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")

# Минимальная транслитерация RU->LAT для осмысленных имён файлов (TZ 7.4).
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _slug(title: str, idx: int, used: set[str]) -> str:
    """Заголовок -> стабильный латинский id. Пустой/коллизия -> imgN."""
    low = title.strip().lower()
    out = []
    for ch in low:
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum():
            out.append(ch)
        else:
            out.append("-")
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")

    if not slug:
        slug = f"img{idx}"
    if slug in used:
        slug = f"{slug}-{idx}"
    return slug


def parse(text: str) -> list[ImageSlot]:
    lines = text.splitlines()
    slots: list[ImageSlot] = []
    used: set[str] = set()
    # Накопленные [INLINE_IMAGE:...] между предыдущим маркером и текущим —
    # станут inline_refs следующего слота.
    pending_inline: list[str] = []

    i = 0
    while i < len(lines):
        # Inline-картинка из docx (поставлена в article_loader) — кладём
        # в очередь до следующего маркера Рис.
        m_img = INLINE_IMAGE_LINE.match(lines[i])
        if m_img:
            pending_inline.append(m_img.group(1).strip())
            i += 1
            continue

        m = MARKER.match(lines[i])
        if not m:
            i += 1
            continue

        # Группа 1 — опц. категория из квадратных скобок, группа 2 — заголовок.
        category = (m.group(1) or "").strip().lower() or None
        title = m.group(2).strip()
        bullets: list[str] = []

        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if not s:
                if bullets:  # пустая строка после буллетов — конец блока
                    break
                j += 1  # пустые между маркером и буллетами — пропускаем
                continue
            # Markdown-таблица под Рис. → каждая строка как буллет.
            if TABLE_ROW.match(s) and not bullets:
                while j < len(lines):
                    ts = lines[j].strip()
                    if not TABLE_ROW.match(ts):
                        break
                    if TABLE_SEPARATOR.match(ts):
                        j += 1
                        continue
                    cells = [c.strip() for c in ts.strip("|").split("|")]
                    cells = [c for c in cells if c]
                    if cells:
                        bullets.append(" — ".join(cells))
                    j += 1
                break  # после таблицы блок закончился
            mb = BULLET_LINE.match(s)
            if mb:
                b = mb.group(1).strip()
                if b:
                    bullets.append(b)
                j += 1
                continue
            break  # непустая строка без буллета — блок закончился

        idx = len(slots) + 1
        sid = _slug(title, idx, used)
        used.add(sid)
        # Тип: по умолчанию из классификатора (порог по буллетам),
        # но категория-алиас может форсировать. `[infographic]`/`[grid]`
        # → INFOGRAPHIC, `[story]`/`[scene]` → STORY. Кастомные категории
        # типа `[characters]` тип не меняют — только папку рефов.
        from .prompt_builder import resolve_category
        slot_type = classify_type(title, bullets)
        resolved = resolve_category(category)
        if resolved == "infographic":
            slot_type = SlotType.INFOGRAPHIC
        elif resolved == "story":
            slot_type = SlotType.STORY
        slots.append(
            ImageSlot(
                id=sid,
                title=title,
                bullets=tuple(bullets),
                type=slot_type,
                category=category,
                inline_refs=tuple(pending_inline),
            )
        )
        # Очередь inline-картинок сбрасывается после ассоциации со слотом.
        pending_inline = []
        i = max(j, i + 1)

    return slots
