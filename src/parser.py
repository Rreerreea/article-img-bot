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


def _wants_inline_ref(title: str, category: str | None) -> bool | None:
    """Привязывать ли картинки-выше как inline-рефы для этого слота.

    True — да (либо ключ во фразе title, либо явная категория [ref]/[references]).
    False — явный noref, игнорировать накопленные картинки.
    None — нейтрально (по умолчанию НЕ привязываем; защита от случайных
    декоративных картинок становящихся рефами).
    """
    if category in ("noref", "no-ref", "без_рефов", "без-рефов"):
        return False
    if category in ("ref", "references", "withref", "with-ref"):
        return True
    title_low = title.lower()
    triggers = (
        "из картинки выше", "с картинки выше", "картинку выше",
        "картинки выше", "картинка выше", "по картинке выше",
        "по картинке", "по схеме выше", "по схеме",
        "референс выше", "за референс", "взять за референс",
        "контент с картинки", "контент из картинки",
        "используй картинку", "use image above", "based on image above",
    )
    if any(t in title_low for t in triggers):
        return True
    return None


def parse(text: str) -> list[ImageSlot]:
    lines = text.splitlines()
    slots: list[ImageSlot] = []
    used: set[str] = set()
    # Накопленные [INLINE_IMAGE:...] между предыдущим маркером и текущим.
    # Привязываем к слоту ТОЛЬКО если автор явно об этом просит —
    # ключевыми словами в title или категорией [ref].
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
        # Привязываем ли inline-рефы? По умолчанию — НЕТ. Только если
        # автор явно просит ключевой фразой или категорией [ref].
        want_inline = _wants_inline_ref(title, category)
        if want_inline:
            slot_inline = tuple(pending_inline)
        else:
            slot_inline = ()
        # `[noref]` / `[ref]` — это категории-флаги, не настоящие папки
        # рефов. Не передаём их в slot.category, иначе сломаем
        # `refs_dir_for`. Сбрасываем в None.
        if category in ("ref", "references", "withref", "with-ref",
                        "noref", "no-ref", "без_рефов", "без-рефов"):
            stored_category: str | None = None
        else:
            stored_category = category

        slots.append(
            ImageSlot(
                id=sid,
                title=title,
                bullets=tuple(bullets),
                type=slot_type,
                category=stored_category,
                inline_refs=slot_inline,
            )
        )
        # Очередь inline-картинок очищаем в любом случае — каждая
        # картинка ассоциируется максимум с одним маркером.
        pending_inline = []
        i = max(j, i + 1)

    return slots
