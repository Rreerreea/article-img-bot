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
    # 3:2 — близко к нативным OpenAI 1536x1024 и Gemini, постпроцесс
    # не режет содержимое; раньше 1280x720 (16:9) обрезал низ инфографик.
    SlotType.INFOGRAPHIC: (1536, 1024),
    SlotType.STORY: (1024, 1024),       # квадрат — образная сцена
}

ASPECT_RATIO: dict[SlotType, str] = {
    SlotType.INFOGRAPHIC: "16:9",
    SlotType.STORY: "1:1",
}


@dataclass(frozen=True)
class PromptSpec:
    prompt: str
    aspect_ratio: str
    target_size: tuple[int, int]
    refs_dir: Path
    refs_signature: str


def refs_dir_for(slot_type: SlotType, base_refs_dir: Path) -> Path:
    """base/infographic либо base/story (заказчик заливает рефы туда)."""
    return Path(base_refs_dir) / slot_type.value


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


def build_prompt(slot: ImageSlot, preset: str | None = None) -> str:
    """Сильный промпт: модель рисует ВЕСЬ текст сама.

    Промпт языко-нейтральный — что прислали, то и рендерим (русский,
    английский, испанский, любой). Никаких хардкодов про язык.
    """
    style = presets.get(preset)
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
            f"{style.infographic}. 3:2 horizontal layout.\n"
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
            "All text must be crisp and accurate. No extra text. "
            "Sharp, professional, magazine-grade quality."
        )
    # Сюжетная: образная иллюстрация по смыслу (текст не нужен).
    bits = [slot.title, *slot.bullets]
    body = ". ".join(b for b in bits if b).strip()
    return f"{style.story}. Scene: {body}" if body else style.story


def build(
    slot: ImageSlot, base_refs_dir: Path, preset: str | None = None
) -> PromptSpec:
    folder = refs_dir_for(slot.type, Path(base_refs_dir))
    return PromptSpec(
        prompt=build_prompt(slot, preset),
        aspect_ratio=ASPECT_RATIO[slot.type],
        target_size=TARGET_SIZE[slot.type],
        refs_dir=folder,
        refs_signature=refs_signature(folder),
    )
