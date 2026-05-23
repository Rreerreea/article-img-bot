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
from .models import ImageSlot

MARKER = re.compile(r"^\s*Рис\.\s?(.*)$")
# Буллет в начале строки: • (docx/HTML), -, *, ·, –, — (Markdown/Docs).
# Обычные абзацы так не начинаются — границу блока не размывает.
BULLET_LINE = re.compile(r"^[•\-\*·–—]\s+(.*)$")

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

    i = 0
    while i < len(lines):
        m = MARKER.match(lines[i])
        if not m:
            i += 1
            continue

        title = m.group(1).strip()
        bullets: list[str] = []

        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if not s:
                if bullets:  # пустая строка после буллетов — конец блока
                    break
                j += 1  # пустые между маркером и буллетами — пропускаем
                continue
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
        slots.append(
            ImageSlot(
                id=sid,
                title=title,
                bullets=tuple(bullets),
                type=classify_type(title, bullets),
            )
        )
        i = max(j, i + 1)

    return slots
