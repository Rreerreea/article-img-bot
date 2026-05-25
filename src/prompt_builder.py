"""Промпт-билдер: слот -> что и как генерить.

По типу слота (TZ 8a) выбирается набор рефов, aspect_ratio,
целевой размер и текст промпта.

Стратегия по тексту (решение «3 в 1», TZ 10.A): для ИНФОГРАФИКИ
модель рисует только визуал/иконки/сетку БЕЗ текста (нейросети
искажают длинный русский), а правильные подписи накладываются
программно из данных ТЗ (text_overlay). Для СЮЖЕТНЫХ текста нет —
промпт описывает сцену.

refs_signature привязывает набор рефов к кэшу воркера: сменили
рефы — изменилась сигнатура — кэш промахивается — перегенерация.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from . import presets
from .models import ImageSlot, SlotType

TARGET_SIZE: dict[SlotType, tuple[int, int]] = {
    # 16:9 для всех — общий рефы-стайл (Гоша зафиксировал).
    # OpenAI gpt-image-2 рисует 1536x1024 (3:2), постпроцесс кропит до 1536x864.
    # Gemini-модели слушаются «16:9» из промпта.
    SlotType.INFOGRAPHIC: (1536, 864),
    SlotType.STORY: (1536, 864),
}

# Глобальное правило типографики для всех инфографик.
# Manrope — modern geometric sans-serif, slightly rounded, очень
# популярная open-source гарнитура. Модели её обычно узнают; даже
# если нет — описание ниже даст схожий стиль.
FONT_NAME = "Manrope"
FONT_DESCRIPTION = (
    "modern geometric sans-serif typeface (Manrope-style): clean, "
    "slightly rounded letterforms, generous spacing, low contrast, "
    "neutral and professional"
)

# Алиасы для системных категорий: позволяет в статье писать `Рис.[Сюжет]`
# вместо `Рис.[story]`. Пользовательские категории — только латиница, как
# создал, без алиасов.
CATEGORY_ALIASES = {
    "story": "story",
    "сюжет": "story",
    "сюжетная": "story",
    "сюжетные": "story",
    "scene": "story",
    "infographic": "infographic",
    "инфографика": "infographic",
    "инфографики": "infographic",
    "info": "infographic",
}


def resolve_category(name: str | None) -> str | None:
    """Алиас → канон. Неизвестное — возвращаем как есть (юзер-категория)."""
    if not name:
        return None
    low = name.strip().lower()
    return CATEGORY_ALIASES.get(low, low)

ASPECT_RATIO: dict[SlotType, str] = {
    SlotType.INFOGRAPHIC: "16:9",
    SlotType.STORY: "16:9",
}


@dataclass(frozen=True)
class PromptSpec:
    prompt: str
    aspect_ratio: str
    target_size: tuple[int, int]
    refs_dir: Path
    refs_signature: str


def refs_dir_for(slot, base_refs_dir: Path) -> Path:
    """Папка рефов для слота.

    Если у слота явно указана category (`Рис.[название]` в статье) и
    эта папка существует — берём её (с раскрытием алиасов: «сюжет»
    → «story»). Иначе fallback на стандартную base/infographic|story.
    """
    base = Path(base_refs_dir)
    cat = resolve_category(getattr(slot, "category", None))
    if cat:
        custom = base / cat
        if custom.is_dir():
            return custom
    return base / slot.type.value


def refs_signature(folder: Path) -> str:
    """Сигнатура набора рефов: имя+размер+mtime файлов. Пусто/нет папки -> ''."""
    folder = Path(folder)
    if not folder.is_dir():
        return ""
    parts: list[str] = []
    for f in sorted(folder.iterdir()):
        if f.is_file():
            st = f.stat()
            parts.append(f"{f.name}:{st.st_size}:{int(st.st_mtime)}")
    if not parts:
        return ""
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


STYLE_HINT_FOR_REFS = (
    "VISUAL STYLE: strictly match the colors, palette, lighting, "
    "composition, illustration approach, and overall mood of the "
    "provided reference images. Copy their aesthetic precisely — "
    "do NOT default to generic stock styling."
)


def build_prompt(
    slot: ImageSlot,
    preset: str | None = None,
    *,
    has_refs: bool = False,
    style_desc: str = "",
) -> str:
    """Сильный промпт: модель рисует ВЕСЬ текст сама.

    Промпт языко-нейтральный — что прислали, то и рендерим (русский,
    английский, испанский, любой). Никаких хардкодов про язык.

    has_refs: если True — добавляется STYLE_HINT_FOR_REFS (просим строго
        копировать визуал прикреплённых рефов).
    style_desc: пользовательский текст-описание стиля (из .style.txt в
        папке рефов) — добавляется как «User style notes: …».
    """
    style = presets.get(preset)
    style_hint = STYLE_HINT_FOR_REFS + "\n" if has_refs else ""
    user_notes = f"User style notes: {style_desc}\n" if style_desc else ""
    if slot.type is SlotType.INFOGRAPHIC:
        n = max(1, len(slot.bullets))
        title = slot.title.strip()
        blocks = "\n".join(
            f"{i}. {b.strip()}" for i, b in enumerate(slot.bullets, 1)
        )
        title_line = (
            f"Prominent title at the top: «{title}».\n" if title else ""
        )
        return (
            f"{style.infographic}. 16:9 horizontal cinematic layout.\n"
            f"{style_hint}{user_notes}"
            "CRITICAL SAFE AREA: the very top ~10% and very bottom ~10% of "
            "the canvas will be cropped. Position the title AND every "
            "content block within the central ~80% vertical region. Leave "
            "GENEROUS empty margin (decorative background only) at the very "
            "top and very bottom — no text, no critical illustrations there.\n"
            f"{title_line}"
            f"{n} content blocks in a well-structured layout; each block has "
            "a distinctive custom thematic illustration (cryptocurrency, "
            "tokens, blockchain, finance motifs), rich and polished — NOT "
            "generic clipart, NOT cluttered.\n"
            "Render EXACTLY the text below — same language, same script, "
            "same characters as given. Do NOT translate, do NOT transliterate. "
            "Perfectly spelled, fully legible, no typos, no distorted or fake "
            "letters, no gibberish — each numbered item is one block caption:\n"
            f"{blocks}\n"
            f"Typography for ALL text on the image: {FONT_NAME} font — "
            f"{FONT_DESCRIPTION}. Use it consistently for both the title "
            "and block captions. "
            "All text must be crisp and accurate. No extra text. "
            "Sharp, professional, magazine-grade quality."
        )
    # Сюжетная: образная иллюстрация по смыслу (текст не нужен).
    bits = [slot.title, *slot.bullets]
    body = ". ".join(b for b in bits if b).strip()
    base = (
        f"{style.story}. 16:9 horizontal cinematic composition. "
        f"{style_hint}{user_notes}"
        "Keep the main subject within the central ~80% vertical area — "
        "the very top and very bottom of the canvas may be cropped."
    )
    return f"{base} Scene: {body}" if body else base


def build(
    slot: ImageSlot, base_refs_dir: Path, preset: str | None = None
) -> PromptSpec:
    folder = refs_dir_for(slot, Path(base_refs_dir))
    # Файл .style.txt в папке рефов — пользовательское описание стиля,
    # ложится в промпт как «User style notes».
    style_desc = ""
    desc_file = folder / ".style.txt"
    if desc_file.is_file():
        style_desc = desc_file.read_text(encoding="utf-8").strip()
    # Есть ли реальные картинки-рефы в этой папке.
    img_exts = {".png", ".jpg", ".jpeg", ".webp"}
    has_refs = folder.is_dir() and any(
        f.is_file() and f.suffix.lower() in img_exts
        for f in folder.iterdir()
    )
    sig = refs_signature(folder)
    # Версия .style.txt влияет на сигнатуру → смена описания → перегенерация.
    if style_desc:
        import hashlib
        sig = hashlib.sha1(
            (sig + "|" + style_desc).encode("utf-8")
        ).hexdigest()[:16]
    return PromptSpec(
        prompt=build_prompt(
            slot, preset, has_refs=has_refs, style_desc=style_desc
        ),
        aspect_ratio=ASPECT_RATIO[slot.type],
        target_size=TARGET_SIZE[slot.type],
        refs_dir=folder,
        refs_signature=sig,
    )
