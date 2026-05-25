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
    "STYLE PRIORITY: treat the provided reference images as the absolute "
    "authority on visual style. Match EVERYTHING from them: medium "
    "(photography vs. illustration vs. 3D render), color palette, lighting "
    "direction and quality, level of detail, depth of field, texture, "
    "background, mood. If refs are photographs — produce a photograph; "
    "if refs are illustrations — produce an illustration. "
    "Do NOT add logos, brand marks, cryptocurrency symbols, sparkle/fairy "
    "light effects, neon glows, or any decorative elements that are not "
    "present in the references."
)


def build_prompt(
    slot: ImageSlot,
    preset: str | None = None,
    *,
    has_refs: bool = False,
    style_desc: str = "",
) -> str:
    """Промпт-каркас. Стиль вытаскиваем из рефов и/или user_notes — без
    жёстких хардкодов «premium 3D / magazine / cryptocurrency motifs»,
    иначе рефы не могут перебить.

    has_refs: если True — STYLE_HINT_FOR_REFS (строго копировать визуал).
    style_desc: пользовательский .style.txt — идёт как «User style notes».
    preset: оставлен для совместимости со скрытым /style; если задан
        нетипично — добавляется как доп. подсказка, иначе нейтрально.
    """
    style_hint = STYLE_HINT_FOR_REFS + "\n" if has_refs else ""
    user_notes = f"User style notes: {style_desc}\n" if style_desc else ""
    # Преcет применяется ТОЛЬКО если выбран явно через /style. По дефолту
    # (DEFAULT preset) ничего не подмешиваем — пусть рефы рулят.
    preset_line = ""
    if preset and preset != presets.DEFAULT:
        p = presets.get(preset)
        if slot.type is SlotType.INFOGRAPHIC and p.infographic:
            preset_line = f"{p.infographic}.\n"
        elif slot.type is SlotType.STORY and p.story:
            preset_line = f"{p.story}.\n"

    if slot.type is SlotType.INFOGRAPHIC:
        n = max(1, len(slot.bullets))
        title = slot.title.strip()
        blocks = "\n".join(
            f"{i}. {b.strip()}" for i, b in enumerate(slot.bullets, 1)
        )
        title_line = (
            f"Title at the top of the image: «{title}».\n" if title else ""
        )
        return (
            "16:9 horizontal layout.\n"
            "SAFE AREA: top ~10% and bottom ~10% of the canvas may be "
            "cropped — keep the title and every content block within the "
            "central ~80% vertical region.\n"
            f"{preset_line}{style_hint}{user_notes}"
            f"{title_line}"
            f"{n} content blocks describing the items below. Each block "
            "has a clear distinctive illustration matching its caption.\n"
            "Render EXACTLY the text below — same language, same script, "
            "same characters as given. Do NOT translate, do NOT "
            "transliterate. Perfectly spelled, fully legible, no typos, "
            "no fake letters. Each numbered item is one block caption:\n"
            f"{blocks}\n"
            f"Text typography: {FONT_NAME} font ({FONT_DESCRIPTION}). "
            "All text must be crisp and accurate. No extra text."
        )
    # Сюжетная: один цельный кадр без подписей. Стиль решают рефы.
    bits = [slot.title, *slot.bullets]
    body = ". ".join(b for b in bits if b).strip()
    return (
        "16:9 horizontal composition.\n"
        "SAFE AREA: top ~10% and bottom ~10% may be cropped — keep the "
        "main subject within the central ~80% vertical region.\n"
        f"{preset_line}{style_hint}{user_notes}"
        + (f"Image content: {body}." if body else "")
    ).rstrip()


def build(
    slot: ImageSlot, base_refs_dir: Path, preset: str | None = None
) -> PromptSpec:
    folder = refs_dir_for(slot, Path(base_refs_dir))
    # Файл .style.txt в папке рефов — пользовательское описание стиля,
    # ложится в промпт как «User style notes».
    style_desc = ""
    desc_file = folder / ".style.txt"
    if desc_file.is_file():
        try:
            style_desc = desc_file.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            # Битый файл (кодировка, перм) — просто игнорируем, не валим
            # генерацию. Свежий .style.txt от бота всегда utf-8.
            style_desc = ""
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
