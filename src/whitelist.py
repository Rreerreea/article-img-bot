"""Whitelist по Telegram user_id (TZ 7.2).

Бот в Телеграме доступен любому, кто узнает @username. Без гейта
чужие жгут платный API-бюджет заказчика. Безопасный дефолт:
пустой список -> не пускаем НИКОГО (а не «всех»).

Для тестов есть ОСОЗНАННЫЙ обход: HF_ALLOWED_USER_IDS=* -> пускать
всех. Это явный режим (не молчаливое «пусто=все»), на проде убрать.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Whitelist:
    allowed: frozenset[int]
    allow_all: bool = False  # тестовый режим, задаётся '*'

    def is_allowed(self, user_id: int | None) -> bool:
        if self.allow_all:
            return True
        return user_id is not None and user_id in self.allowed

    @property
    def is_empty(self) -> bool:
        # allow_all — это НЕ пусто (осознанно открыто), бот стартует.
        return not self.allowed and not self.allow_all

    @classmethod
    def from_env(cls, var: str = "HF_ALLOWED_USER_IDS") -> "Whitelist":
        raw = os.getenv(var, "")
        if "*" in raw:
            return cls(frozenset(), allow_all=True)
        ids = {
            int(p.strip())
            for p in raw.split(",")
            if p.strip().lstrip("-").isdigit()
        }
        return cls(frozenset(ids))
