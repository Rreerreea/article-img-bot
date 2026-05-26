"""Каталог моделей-генераторов для выбора пользователем в боте.

Сейчас 4 варианта в меню (порядок зафиксирован Гошей 2026-05-26):
1. GPT-Image — OpenAI direct, с рефами через images.edit
2. Nano Banana Pro — Google Gemini ЧЕРЕЗ Krea-аггрегатор
3. Flux 1.1 Pro — фото-реализм через Krea
4. Ideogram 3.0 — лучший рендер текста через Krea

Старые Gemini-варианты (через прямой Google ключ) скрыты — все Gemini-
дороги ведут через Krea, чтобы один ключ покрывал всё.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Provider


@dataclass(frozen=True)
class ModelChoice:
    """Один вариант для меню «Модель»."""

    key: str           # стабильный id для callback_data и кэша
    label: str         # текст для кнопки
    provider: Provider
    model: str         # имя модели у провайдера (для Krea — путь vendor/model)
    quality: str       # для OpenAI; иначе игнорируется
    price_per_image: float
    time_per_image_sec: int = 30
    needs_krea: bool = False    # скрыть если нет KREA_API_KEY
    needs_gemini: bool = False  # скрыть если нет GEMINI_API_KEY (direct)
    supports_edit: bool = False


CHOICES: dict[str, ModelChoice] = {
    # 1) OpenAI напрямую — с рефами через images.edit. Силён в тексте.
    "gpt_high": ModelChoice(
        key="gpt_high",
        label="GPT-Image high ~$0.17",
        provider=Provider.OPENAI,
        model="gpt-image-2",
        quality="high",
        price_per_image=0.167,
        time_per_image_sec=80,
    ),
    # 2) Nano Banana Pro через Krea — премиум универсал.
    "nano_pro": ModelChoice(
        key="nano_pro",
        label="Nano Banana Pro ~$0.15",
        provider=Provider.KREA,
        model="google/nano-banana-pro",
        quality="",
        price_per_image=0.15,
        time_per_image_sec=30,
        needs_krea=True,
    ),
    # 3) Flux 1.1 Pro через Krea — лучший фото-реализм.
    "flux_pro": ModelChoice(
        key="flux_pro",
        label="Flux Pro ~$0.06",
        provider=Provider.KREA,
        model="bfl/flux-1.1-pro",
        quality="",
        price_per_image=0.06,
        time_per_image_sec=15,
        needs_krea=True,
    ),
    # 4) Ideogram 3.0 через Krea — лучший рендер текста на картинках.
    "ideogram_3": ModelChoice(
        key="ideogram_3",
        label="Ideogram 3.0 ~$0.06",
        provider=Provider.KREA,
        model="ideogram/ideogram-3",
        quality="",
        price_per_image=0.06,
        time_per_image_sec=20,
        needs_krea=True,
    ),
}

DEFAULT = "gpt_high"


def canon(name: str | None) -> str:
    """Безопасно сводим к существующему ключу; неизвестное → дефолт."""
    if not name:
        return DEFAULT
    return name if name in CHOICES else DEFAULT


def get(name: str | None) -> ModelChoice:
    return CHOICES[canon(name)]


def available(
    has_gemini_key: bool = False, has_krea_key: bool = False
) -> list[ModelChoice]:
    """Что реально доступно на текущем .env. Krea-варианты требуют KREA_API_KEY."""
    out = []
    for c in CHOICES.values():
        if c.needs_krea and not has_krea_key:
            continue
        if c.needs_gemini and not has_gemini_key:
            continue
        out.append(c)
    return out
