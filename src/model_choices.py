"""Каталог моделей-генераторов для выбора пользователем в боте.

Каждый вариант = провайдер + модель + (для OpenAI) качество + цена.
В UI показывается человеческим лейблом; внутри пайплайна оверрайдит cfg.
Гемини-варианты доступны только если есть GEMINI_API_KEY.
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
    model: str
    quality: str       # для OpenAI; для Gemini игнорируется
    price_per_image: float
    time_per_image_sec: int = 30  # примерное время на одну картинку
    needs_gemini: bool = False  # скрывать, если нет GEMINI_API_KEY
    supports_edit: bool = False  # умеет ли модель править готовую картинку


CHOICES: dict[str, ModelChoice] = {
    "gpt_med": ModelChoice(
        key="gpt_med",
        label="GPT-2 medium ~$0.05",
        provider=Provider.OPENAI,
        model="gpt-image-2",
        quality="medium",
        price_per_image=0.053,
        time_per_image_sec=40,
    ),
    "gpt_high": ModelChoice(
        key="gpt_high",
        label="GPT-2 high ~$0.17",
        provider=Provider.OPENAI,
        model="gpt-image-2",
        quality="high",
        price_per_image=0.167,
        time_per_image_sec=80,
    ),
    "nano_flash": ModelChoice(
        key="nano_flash",
        label="Nano Banana 2 ~$0.04",
        provider=Provider.GEMINI,
        model="gemini-3.1-flash-image-preview",
        quality="",
        price_per_image=0.04,
        time_per_image_sec=15,
        needs_gemini=True,
        supports_edit=True,
    ),
    "nano_pro": ModelChoice(
        key="nano_pro",
        label="Nano Banana Pro ~$0.13",
        provider=Provider.GEMINI,
        model="gemini-3-pro-image-preview",
        quality="",
        price_per_image=0.134,
        time_per_image_sec=60,
        needs_gemini=True,
        supports_edit=True,
    ),
}

DEFAULT = "gpt_med"


def canon(name: str | None) -> str:
    """Безопасно сводим к существующему ключу; неизвестное → дефолт."""
    if not name:
        return DEFAULT
    return name if name in CHOICES else DEFAULT


def get(name: str | None) -> ModelChoice:
    return CHOICES[canon(name)]


def available(has_gemini_key: bool) -> list[ModelChoice]:
    """Список того, что реально можно использовать на этом .env."""
    return [c for c in CHOICES.values() if has_gemini_key or not c.needs_gemini]
