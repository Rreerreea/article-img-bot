"""Конфигурация из окружения (.env).

Развилки:
- HF_MODE: MOCK (бесплатно, дефолт разработки) или REAL (платно/боевой).
- HF_PROVIDER: gemini (Nano Banana, дефолт — есть free tier) или
  higgsfield (запасной вариант). Переключается одной строкой.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

try:
    from dotenv import load_dotenv

    # Грузим .env ДЕТЕРМИНИРОВАННО от корня проекта, не от cwd:
    # иначе .env подхватывается только при запуске из нужной папки.
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ModuleNotFoundError:
    # python-dotenv не обязателен: переменные можно задать и снаружи.
    pass


class Mode(str, Enum):
    MOCK = "MOCK"
    REAL = "REAL"


class Provider(str, Enum):
    GEMINI = "gemini"          # Nano Banana (Gemini Image, прямой Google ключ)
    OPENAI = "openai"          # GPT Image 2 — силён в тексте
    KREA = "krea"              # Аггрегатор: Flux Pro, Nano Banana Pro, Ideogram
    HIGGSFIELD = "higgsfield"  # запасной


@dataclass(frozen=True)
class Config:
    mode: Mode
    credentials: str
    model: str
    quality: str
    concurrency: int
    max_retries: int
    price_per_image: float
    base_dir: Path
    # Новые поля — с дефолтами, чтобы прямые Config(...) в тестах не падали.
    provider: Provider = Provider.GEMINI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-image-preview"  # Nano Banana Pro
    openai_api_key: str = ""
    openai_model: str = "gpt-image-2"
    openai_quality: str = "medium"  # low|medium|high — баланс цена/качество
    krea_api_key: str = ""
    # Krea-модель — путь типа "bfl/flux-1.1-pro", "google/nano-banana-pro",
    # "ideogram/ideogram-3". Подставляется в /generate/image/{krea_model}.
    krea_model: str = "bfl/flux-1.1-pro"
    # Гибрид 10.A: правильный текст ТЗ поверх инфографики. Можно
    # отключить (HF_TEXT_OVERLAY=0), чтобы сравнить с чистым визуалом.
    text_overlay: bool = True

    @property
    def refs_dir(self) -> Path:
        return self.base_dir / "refs"

    @property
    def output_dir(self) -> Path:
        return self.base_dir / "output"

    @property
    def cache_dir(self) -> Path:
        return self.base_dir / ".cache"

    def ensure_dirs(self) -> None:
        for d in (self.output_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, base_dir: str | Path | None = None) -> "Config":
        root = Path(base_dir) if base_dir else Path(__file__).resolve().parent.parent
        cfg = cls(
            mode=Mode(os.getenv("HF_MODE", "MOCK").upper()),
            credentials=os.getenv("HF_CREDENTIALS", ""),
            model=os.getenv("HF_MODEL", "flux-pro/kontext/max/text-to-image"),
            quality=os.getenv("HF_QUALITY", "economy"),
            concurrency=int(os.getenv("HF_CONCURRENCY", "3")),
            max_retries=int(os.getenv("HF_MAX_RETRIES", "2")),
            price_per_image=float(os.getenv("HF_PRICE_PER_IMAGE", "0.0")),
            base_dir=root,
            provider=Provider(os.getenv("HF_PROVIDER", "gemini").lower()),
            gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.1-flash-image-preview"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-image-2"),
            openai_quality=os.getenv("OPENAI_QUALITY", "medium"),
            krea_api_key=os.getenv("KREA_API_KEY", ""),
            krea_model=os.getenv("KREA_MODEL", "bfl/flux-1.1-pro"),
            text_overlay=os.getenv("HF_TEXT_OVERLAY", "1") not in ("0", "false", "False"),
        )
        # REAL без нужных ключей — дорогая/глупая ошибка. Падаем явно.
        if cfg.mode is Mode.REAL:
            if cfg.provider is Provider.GEMINI and not cfg.gemini_api_key:
                raise RuntimeError(
                    "HF_MODE=REAL, провайдер gemini, но GEMINI_API_KEY пуст. "
                    "Возьмите бесплатный ключ в Google AI Studio или "
                    "вернитесь в HF_MODE=MOCK."
                )
            if cfg.provider is Provider.HIGGSFIELD and not cfg.credentials:
                raise RuntimeError(
                    "HF_MODE=REAL, провайдер higgsfield, но HF_CREDENTIALS "
                    "пуст. Заполните ключи или вернитесь в HF_MODE=MOCK."
                )
            if cfg.provider is Provider.OPENAI and not cfg.openai_api_key:
                raise RuntimeError(
                    "HF_MODE=REAL, провайдер openai, но OPENAI_API_KEY пуст. "
                    "Заполните ключ или вернитесь в HF_MODE=MOCK."
                )
            if cfg.provider is Provider.KREA and not cfg.krea_api_key:
                raise RuntimeError(
                    "HF_MODE=REAL, провайдер krea, но KREA_API_KEY пуст. "
                    "Заполните ключ в .env или выберите другую модель."
                )
        return cfg
