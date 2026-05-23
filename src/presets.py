"""Пресеты стиля. Меняют ТОЛЬКО художественную обёртку промпта —
структура и требование точного русского текста остаются общими
(см. prompt_builder). Переключаются командой /style или env STYLE_PRESET.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Preset:
    label: str            # человекочитаемое имя для /style
    infographic: str      # стиль-префикс для инфографики
    story: str            # стиль-префикс для сюжетной


PRESETS: dict[str, Preset] = {
    "premium": Preset(
        "Премиум 3D",
        "Award-winning editorial infographic for a premium technology and "
        "finance publication. Art-directed, high production value, strong "
        "visual hierarchy, cohesive modern design system, refined palette "
        "with one bold accent color, subtle depth (soft shadows, gentle "
        "gradients), confident balanced composition with a clear focal point",
        "Award-winning editorial conceptual illustration, art-directed, "
        "cinematic lighting, striking composition, rich detail, premium "
        "magazine quality",
    ),
    "flat": Preset(
        "Минимал-флэт",
        "Clean minimal flat vector infographic, 2D, simple geometric shapes, "
        "generous whitespace, modern restrained palette, crisp and tidy, "
        "no gradients, no 3D",
        "Minimal flat vector illustration, simple shapes, limited palette, "
        "modern and clean",
    ),
    "dark": Preset(
        "Тёмный-неон",
        "Dark-theme technology infographic, deep dark background, glowing "
        "neon accents, futuristic high-tech aesthetic, high contrast, sleek",
        "Dark cinematic illustration, neon glow, futuristic moody atmosphere",
    ),
    "corporate": Preset(
        "Корпоративный",
        "Clean corporate business infographic, conservative blue and grey "
        "palette, professional, restrained, trustworthy, structured layout",
        "Professional corporate illustration, restrained palette, sober tone",
    ),
}

DEFAULT = "premium"


def canon(name: str | None) -> str:
    """Каноническое имя пресета (неизвестное/пустое -> дефолт).

    Нужно для namespace кэша: preset='bogus' и preset=None должны
    давать тот же кэш, что и дефолтный (промпт у них одинаковый).
    """
    key = (name or "").strip().lower()
    return key if key in PRESETS else DEFAULT


def get(name: str | None) -> Preset:
    """Пресет по имени; неизвестный/пустой -> дефолт (не падаем)."""
    return PRESETS[canon(name)]


def names() -> list[str]:
    return list(PRESETS)
