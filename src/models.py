"""Доменные модели пайплайна «статья - картинки».

Парсер (Task 4) выдаёт список ImageSlot. Воркер превращает каждый
слот в GenerationResult. Эти типы — контракт между модулями ядра,
он не меняется при смене форм-фактора (TZ раздел 4).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SlotType(str, Enum):
    """Тип задания на картинку (TZ 8a). Определяется автоклассификатором."""

    INFOGRAPHIC = "infographic"  # перечень данных: токены, пункты, функции
    STORY = "story"              # сюжетная/образная иллюстрация


class GenStatus(str, Enum):
    """Исход генерации одного слота."""

    OK = "ok"                  # сгенерировано в этом прогоне
    FROM_CACHE = "from_cache"  # взято из кэша, API не дёргали (экономия, TZ 7.7б)
    FAILED = "failed"          # не вышло даже после ретраев


@dataclass(frozen=True)
class ImageSlot:
    """Одно задание на картинку, извлечённое из статьи под маркером «Рис.».

    id      — стабильный идентификатор (станет именем файла, TZ 7.4);
    title   — заголовок после «Рис.» (может быть пустым);
    bullets — пункты-содержание под маркером;
    type    — инфографика или сюжетная.
    """

    id: str
    title: str
    bullets: tuple[str, ...]
    type: SlotType

    def cache_key(self, refs_signature: str = "") -> str:
        """Ключ кэша. Одинаковый блок при тех же рефах не генерится повторно.

        Учитывает тип, заголовок, пункты и сигнатуру набора рефов:
        смена рефов = другой результат = другой ключ.
        """
        joiner = chr(32)  # пробел
        payload = joiner.join(
            [self.type.value, self.title, *self.bullets, refs_signature]
        )
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


@dataclass
class GenerationResult:
    """Результат по одному слоту."""

    slot_id: str
    status: GenStatus
    file_path: Path | None = None
    attempts: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in (GenStatus.OK, GenStatus.FROM_CACHE)


@dataclass
class Estimate:
    """Смета перед прогоном (TZ 7.7в). Бот показывает её и ждёт /go."""

    total: int
    cached: int
    to_generate: int
    approx_cost_usd: float
    extra: dict = field(default_factory=dict)

    def human(self) -> str:
        lines = [f"🖼 Картинок: {self.total}"]
        if self.cached:
            lines.append(
                f"♻️ Повтор из прошлых прогонов (бесплатно): {self.cached}"
            )
        if self.to_generate:
            cost = (
                f" — ~${self.approx_cost_usd:.2f}"
                if self.approx_cost_usd > 0 else ""
            )
            lines.append(f"💰 К новой генерации: {self.to_generate}{cost}")
        return "\n".join(lines)
