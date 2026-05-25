"""Оркестратор пайплайна — без Telegram.

Вся логика flow здесь, чтобы её можно было тестировать на моках:
prepare() = загрузка + парсинг + смета (показать и ждать /go),
run()     = генерация пачки + сборка ZIP (выдача документом, TZ 7.1).
Telegram-слой поверх этого — тонкая плёнка.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

import dataclasses

from .article_loader import load_article, load_from_url
from .config import Config
from .higgsfield_worker import HiggsfieldWorker
from .model_choices import ModelChoice
from .models import Estimate, GenerationResult, GenStatus, ImageSlot
from .parser import parse


@dataclass
class RunResult:
    zip_path: Path | None
    results: list[GenerationResult]

    @property
    def ok(self) -> int:
        return sum(1 for r in self.results if r.status is GenStatus.OK)

    @property
    def from_cache(self) -> int:
        return sum(1 for r in self.results if r.status is GenStatus.FROM_CACHE)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status is GenStatus.FAILED)

    def human(self) -> str:
        return (
            f"Готово: {self.ok} новых, {self.from_cache} из кэша, "
            f"{self.failed} не вышло."
        )


class PipelineService:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.worker = HiggsfieldWorker(config)

    def _worker_for(self, choice: ModelChoice | None) -> HiggsfieldWorker:
        if choice is None:
            return self.worker
        overrides: dict = {
            "provider": choice.provider,
            "price_per_image": choice.price_per_image,
        }
        # Поля модели — у Gemini и OpenAI разные, оверрайдим только нужное.
        from .config import Provider
        if choice.provider is Provider.OPENAI:
            overrides["openai_model"] = choice.model
            overrides["openai_quality"] = choice.quality or "medium"
        elif choice.provider is Provider.GEMINI:
            overrides["gemini_model"] = choice.model
        return HiggsfieldWorker(dataclasses.replace(self.cfg, **overrides))

    def prepare(
        self,
        source: str | Path | None = None,
        *,
        text: str | None = None,
        preset: str | None = None,
        choice: ModelChoice | None = None,
    ) -> tuple[list[ImageSlot], Estimate]:
        """Источник -> слоты + смета. Смету бот показывает и ждёт /go (TZ 7.7в)."""
        if text is None:
            if source is None:
                raise ValueError("Нужен source (файл/ссылка) или text.")
            s = str(source)
            if s.startswith("http://") or s.startswith("https://"):
                text = load_from_url(s)
            else:
                text = load_article(source)

        slots = parse(text)
        return slots, self._worker_for(choice).estimate(slots, preset)

    async def run(
        self,
        slots: list[ImageSlot],
        preset: str | None = None,
        progress_cb=None,
        choice: ModelChoice | None = None,
    ) -> RunResult:
        worker = self._worker_for(choice)
        results = await worker.generate_batch(slots, preset, progress_cb)
        zip_path = self._build_zip(results)
        return RunResult(zip_path=zip_path, results=results)

    def available_slot_ids(self) -> list[str]:
        """id уже сгенерированных картинок (для /edit)."""
        d = self.cfg.output_dir
        if not d.is_dir():
            return []
        return sorted(p.stem for p in d.glob("*.png"))

    async def edit(
        self,
        slot_id: str,
        instruction: str,
        choice: ModelChoice | None = None,
        slot: ImageSlot | None = None,
        preset: str | None = None,
    ) -> Path | None:
        """Правка картинки.

        Стратегия:
        1) Пробуем нативный img2img у воркера (Gemini Nano Banana умеет,
           MOCK тоже работает всегда).
        2) Если воркер сказал «не поддерживаю» (RuntimeError) — фолбэк
           на перегенерацию с инструкцией в промпте (нужен slot+preset).

        None — если картинки нет или фолбэк невозможен.
        """
        from . import postprocess, prompt_builder

        src = self.cfg.output_dir / f"{slot_id}.png"
        if not src.exists():
            return None

        worker = self._worker_for(choice)

        # Сначала пробуем нативную правку: единственный путь который
        # сохраняет композицию точно. В MOCK работает всегда; в REAL
        # — пока только Gemini.
        try:
            from PIL import Image
            size = Image.open(src).size
            data = await worker.edit_image(src.read_bytes(), instruction)
            data = postprocess.normalize(data, size)
            src.write_bytes(data)
            return src
        except RuntimeError:
            # Воркер явно сказал «не умею» (OpenAI, Higgsfield в REAL).
            # Пробуем фолбэк — перегенерация с инструкцией.
            if slot is None:
                return None

        base_spec = prompt_builder.build(slot, self.cfg.refs_dir, preset)
        merged_prompt = (
            f"{base_spec.prompt}\n\n"
            f"Also apply this change: {instruction}. "
            "Keep the overall structure and existing text unchanged unless "
            "the change explicitly requests otherwise."
        )
        new_spec = dataclasses.replace(base_spec, prompt=merged_prompt)
        data = await worker._generate(slot, new_spec)
        data = postprocess.normalize(data, new_spec.target_size)
        src.write_bytes(data)
        return src

    def _build_zip(self, results: list[GenerationResult]) -> Path | None:
        done = [
            r for r in results if r.ok and r.file_path and r.file_path.exists()
        ]
        if not done:
            return None

        zip_path = self.cfg.base_dir / "result.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for r in done:
                zf.write(r.file_path, arcname=r.file_path.name)
        return zip_path
