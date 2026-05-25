"""Higgsfield-воркер: слот -> картинка.

Закрывает требования TZ:
- async job + polling + retry + лимит конкурентности (раздел 4, 6);
- кэш с учётом рефов: одинаковый блок при тех же рефах не генерится
  повторно; сменили рефы -> сигнатура другая -> перегенерация (7.7б);
- 1 генерация на слот по умолчанию (7.6);
- постобработка под целевой размер (7.5);
- MOCK-режим: весь пайплайн без вызовов API и без затрат.

REAL-ветка написана по официальному SDK, но Higgsfield-документация
скудная (TZ раздел 6) — её нужно сверить на живом оплаченном аккаунте,
поэтому она помечена и не выполняется в тестах.
"""

from __future__ import annotations

import asyncio
import io
import shutil

from . import postprocess, presets, prompt_builder, text_overlay
from .config import Config, Mode, Provider
from .models import Estimate, GenerationResult, GenStatus, ImageSlot, SlotType
from .prompt_builder import PromptSpec


def _is_non_retryable(exc: Exception) -> bool:
    """Ошибки которые ретрай не починит — auth, billing, malformed request.
    Сетевые/таймауты/rate-limit оставляем ретраиться."""
    name = type(exc).__name__
    # Имена классов из openai-python SDK без жёсткого импорта (он опц).
    if name in {
        "AuthenticationError",
        "PermissionDeniedError",
        "BadRequestError",
        "NotFoundError",
        "UnprocessableEntityError",
    }:
        return True
    # Сообщение часто содержит маркер квоты — это тоже стоп.
    msg = str(exc).lower()
    if "billing_hard_limit" in msg or "insufficient_quota" in msg:
        return True
    return False


class HiggsfieldWorker:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.cfg.ensure_dirs()

    def _cache_ns(self, preset: str | None = None):
        """Кэш разделён по режиму, провайдеру, МОДЕЛИ и ПРЕСЕТУ: MOCK не
        подменяет REAL, Gemini ≠ Higgsfield, смена модели/стиля не отдаёт
        старые картинки из кэша."""
        if self.cfg.provider is Provider.GEMINI:
            model = self.cfg.gemini_model
        elif self.cfg.provider is Provider.OPENAI:
            model = self.cfg.openai_model
        else:
            model = self.cfg.model
        d = (
            self.cfg.cache_dir
            / self.cfg.mode.value
            / self.cfg.provider.value
            / model.replace("/", "_")
            / presets.canon(preset)
        )
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- публичное API ---------------------------------------------------

    async def generate_one(
        self, slot: ImageSlot, preset: str | None = None
    ) -> GenerationResult:
        """Один слот: кэш -> (генерация с ретраями) -> нормализация -> output/."""
        spec = prompt_builder.build(slot, self.cfg.refs_dir, preset)
        key = slot.cache_key(spec.refs_signature)
        cache_file = self._cache_ns(preset) / f"{key}.png"
        out_file = self.cfg.output_dir / f"{slot.id}.png"

        # Кэш-хит: API не трогаем (экономия, TZ 7.7б).
        if cache_file.exists():
            shutil.copyfile(cache_file, out_file)
            return GenerationResult(slot.id, GenStatus.FROM_CACHE, out_file)

        last_error: Exception | None = None
        for attempt in range(1, self.cfg.max_retries + 2):  # 1 попытка + ретраи
            try:
                data = await self._generate(slot, spec)
                data = postprocess.normalize(data, spec.target_size)
                # Гибрид 10.A: правильный текст из ТЗ поверх визуала —
                # только инфографика (у сюжетных текста нет).
                if self.cfg.text_overlay and slot.type is SlotType.INFOGRAPHIC:
                    data = text_overlay.render(data, slot, spec.target_size)
                cache_file.write_bytes(data)
                shutil.copyfile(cache_file, out_file)
                return GenerationResult(
                    slot.id, GenStatus.OK, out_file, attempts=attempt
                )
            except Exception as exc:  # noqa: BLE001 — фиксируем и ретраим
                last_error = exc
                # Не-восстановимые ошибки — ретраить бессмысленно, только
                # сжигаем время и (для платных) деньги. Выходим сразу.
                if _is_non_retryable(exc):
                    break

        return GenerationResult(
            slot.id,
            GenStatus.FAILED,
            attempts=attempt,
            error=str(last_error),
        )

    async def generate_batch(
        self,
        slots: list[ImageSlot],
        preset: str | None = None,
        progress_cb=None,
        slot_done_cb=None,
    ) -> list[GenerationResult]:
        """Пачка с лимитом конкурентности (TZ: ~10–30 слотов потоком).

        progress_cb(done, total) — опц., прогресс-бар в боте.
        slot_done_cb(result) — опц., вызывается СРАЗУ как готов каждый
            слот (стримим файл юзеру не дожидаясь всей пачки). Может
            быть sync или async.
        """
        # Защитная проверка диска до того как сжигать API-копейки. Меньше
        # 100 MB свободного — кэш+output+ZIP не влезут, лучше упасть заранее
        # с понятной диагностикой.
        free_mb = shutil.disk_usage(self.cfg.base_dir).free / 1024 / 1024
        if free_mb < 100:
            raise RuntimeError(
                f"Свободного места на диске мало: {free_mb:.0f} MB. "
                "Освободи место и попробуй снова."
            )
        sem = asyncio.Semaphore(self.cfg.concurrency)
        total = len(slots)
        done = 0

        async def run(slot: ImageSlot) -> GenerationResult:
            nonlocal done
            async with sem:
                res = await self.generate_one(slot, preset)
            done += 1
            if progress_cb is not None:
                out = progress_cb(done, total)
                if asyncio.iscoroutine(out):
                    await out
            if slot_done_cb is not None:
                out = slot_done_cb(res)
                if asyncio.iscoroutine(out):
                    await out
            return res

        return await asyncio.gather(*(run(s) for s in slots))

    def estimate(
        self, slots: list[ImageSlot], preset: str | None = None
    ) -> Estimate:
        """Смета до запуска (TZ 7.7в). Кэш-хиты бесплатны и в счёт не идут."""
        cached = 0
        ns = self._cache_ns(preset)
        for s in slots:
            spec = prompt_builder.build(s, self.cfg.refs_dir, preset)
            if (ns / f"{s.cache_key(spec.refs_signature)}.png").exists():
                cached += 1
        to_generate = len(slots) - cached
        return Estimate(
            total=len(slots),
            cached=cached,
            to_generate=to_generate,
            approx_cost_usd=to_generate * self.cfg.price_per_image,
        )

    # ---- внутреннее ------------------------------------------------------

    async def _generate(self, slot: ImageSlot, spec: PromptSpec) -> bytes:
        if self.cfg.mode is Mode.MOCK:
            return self._render_mock(slot)
        if self.cfg.provider is Provider.GEMINI:
            return await self._generate_gemini(spec)
        if self.cfg.provider is Provider.OPENAI:
            return await self._generate_openai(spec)
        return await self._generate_higgsfield(spec)

    def _render_mock(self, slot: ImageSlot) -> bytes:
        """Заглушка через Pillow. Нарочно нецелевой размер — постобработка
        затем приводит к target, поэтому она реально проверяется в тестах."""
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (900, 600), (28, 30, 38))
        draw = ImageDraw.Draw(img)
        lines = [
            "MOCK — API не вызывался",
            f"id: {slot.id}",
            f"тип: {slot.type.value}",
            f"заголовок: {slot.title or '—'}",
            "",
            *[f"• {b}" for b in slot.bullets[:8]],
        ]
        draw.multiline_text(
            (40, 40), "\n".join(lines), fill=(235, 235, 240), spacing=8
        )
        draw.rectangle([(0, 0), (899, 599)], outline=(90, 95, 110))

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _extract_image_url(result) -> str:
        """Достаёт URL картинки из ответа Higgsfield.

        Точная форма ответа официальной докой НЕ подтверждена (TZ 6).
        Пробуем известные варианты; иначе НЕ молчим, а кидаем явную
        ошибку с формой ответа — первый живой прогон сразу покажет
        фактическую структуру, и мы её доточим.
        """
        try:
            if isinstance(result, dict):
                imgs = result.get("images")
                if imgs:
                    return imgs[0]["url"]
                res = result.get("result")
                if isinstance(res, dict):
                    if res.get("url"):
                        return res["url"]
                    raw = res.get("raw")
                    if isinstance(raw, dict) and raw.get("url"):
                        return raw["url"]
                if result.get("url"):
                    return result["url"]
        except (KeyError, IndexError, TypeError):
            pass
        shape = list(result) if isinstance(result, dict) else type(result).__name__
        raise RuntimeError(
            "Higgsfield: не нашёл URL картинки в ответе — сверить форму "
            f"на живом аккаунте. Структура ответа: {shape}"
        )

    async def _generate_higgsfield(self, spec: PromptSpec) -> bytes:
        """РЕАЛЬНЫЙ Higgsfield (платно, запасной провайдер). Ленивый импорт.

        API сверен с фактическим пакетом higgsfield-client 0.1.0
        (2026-05-19). НЕ подтверждено вживую (нет ключа): точная форма
        ответа (см. _extract_image_url) и передача рефов (upload_image).
        """
        import httpx
        import higgsfield_client

        # api_key="KEY_ID:KEY_SECRET"; пусто -> SDK берёт из env (HF_KEY).
        client = higgsfield_client.AsyncClient(
            api_key=self.cfg.credentials or None
        )
        # subscribe сам делает submit + polling и ждёт завершения.
        result = await client.subscribe(
            self.cfg.model,
            {
                "prompt": spec.prompt,
                "aspect_ratio": spec.aspect_ratio,
                "quality": self.cfg.quality,
            },
        )
        url = self._extract_image_url(result)
        async with httpx.AsyncClient(timeout=120) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            return resp.content

    # ---- Gemini / Nano Banana (основной провайдер) -----------------------

    async def _generate_gemini(self, spec: PromptSpec) -> bytes:
        """Gemini 2.5 Flash Image. Рефы — PIL.Image прямо в contents.

        API сверен с google-genai 1.47 (2026-05-19). Точная форма
        ответа подтверждается первым живым прогоном — парсер устойчив
        и при несовпадении даёт явную диагностику, не молчит.
        Размер не задаём: postprocess приводит к target (TZ 7.5).
        """
        from google import genai

        client = genai.Client(api_key=self.cfg.gemini_api_key or None)

        contents: list = [spec.prompt]
        contents.extend(self._load_ref_images(spec.refs_dir))

        resp = await client.aio.models.generate_content(
            model=self.cfg.gemini_model,
            contents=contents,
        )
        return self._extract_gemini_bytes(resp)

    @staticmethod
    def _load_ref_images(refs_dir, limit: int = 4) -> list:
        """До `limit` рефов из refs/<type>/ как PIL.Image (TZ 8a, 'по рефам')."""
        from pathlib import Path

        from PIL import Image

        folder = Path(refs_dir)
        if not folder.is_dir():
            return []
        exts = {".png", ".jpg", ".jpeg", ".webp"}
        imgs = []
        for f in sorted(folder.iterdir()):
            if f.suffix.lower() in exts and f.is_file():
                im = Image.open(f)
                im.load()
                imgs.append(im)
                if len(imgs) >= limit:
                    break
        return imgs

    @staticmethod
    def _extract_gemini_bytes(resp) -> bytes:
        """Достаёт байты картинки из ответа Gemini.

        Пробуем resp.parts и resp.candidates[*].content.parts; берём
        первый inline_data. Иначе НЕ молчим — явная ошибка с диагностикой
        (текст ответа часто = причина отказа модели).
        """
        parts = list(getattr(resp, "parts", None) or [])
        if not parts:
            for cand in getattr(resp, "candidates", None) or []:
                content = getattr(cand, "content", None)
                parts.extend(getattr(content, "parts", None) or [])

        for p in parts:
            inline = getattr(p, "inline_data", None)
            data = getattr(inline, "data", None)
            if data:
                return data

        texts = [
            getattr(p, "text", None) for p in parts if getattr(p, "text", None)
        ]
        raise RuntimeError(
            "Gemini не вернул изображение. "
            + (f"Текст ответа: {' '.join(texts)[:300]}" if texts else
               f"Структура: {type(resp).__name__}, частей: {len(parts)}")
        )

    # ---- OpenAI GPT Image 2 (силён в тексте) -----------------------------

    async def _generate_openai(self, spec: PromptSpec) -> bytes:
        """OpenAI GPT Image 2.

        Если в папке рефов есть картинки — идём через images.edit с
        ними как стилистическими примерами (модель видит визуал).
        Если рефов нет — обычный images.generate (только текст).
        Размер из доступных OpenAI; postprocess приводит к target.
        """
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self.cfg.openai_api_key or None)
        size = "1536x1024" if spec.aspect_ratio == "16:9" else "1024x1024"

        refs = self._load_ref_payloads(spec.refs_dir, limit=4)
        if refs:
            resp = await client.images.edit(
                model=self.cfg.openai_model,
                image=refs,
                prompt=spec.prompt,
                size=size,
                quality=self.cfg.openai_quality,
                n=1,
            )
        else:
            resp = await client.images.generate(
                model=self.cfg.openai_model,
                prompt=spec.prompt,
                size=size,
                quality=self.cfg.openai_quality,
                n=1,
            )
        return self._extract_openai_bytes(resp)

    @staticmethod
    def _load_ref_payloads(refs_dir, limit: int = 4) -> list:
        """Рефы для OpenAI images.edit: список (имя, байты, mime).

        SDK принимает file-like / tuple-payload по спецификации openai-python.
        Возвращаем максимум `limit` файлов из refs_dir.
        """
        from pathlib import Path

        folder = Path(refs_dir)
        if not folder.is_dir():
            return []
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }
        out: list = []
        for f in sorted(folder.iterdir()):
            ext = f.suffix.lower()
            if ext in mime and f.is_file():
                out.append((f.name, f.read_bytes(), mime[ext]))
                if len(out) >= limit:
                    break
        return out

    @staticmethod
    def _extract_openai_bytes(resp) -> bytes:
        """b64_json -> bytes. Не молчим: иная форма -> явная диагностика."""
        try:
            b64 = resp.data[0].b64_json
            if b64:
                import base64

                return base64.b64decode(b64)
        except (AttributeError, IndexError, TypeError):
            pass
        raise RuntimeError(
            "OpenAI не вернул картинку (нет data[0].b64_json) — "
            f"структура: {type(resp).__name__}"
        )

    # ---- Правки картинок (Фича 15) ---------------------------------------

    async def edit_image(self, image_bytes: bytes, instruction: str) -> bytes:
        """Картинка + текст-правка -> новая версия.

        Nano Banana умеет img2img нативно (contents=[image, текст]).
        MOCK — детерминированная пометка для тестов. Не-Gemini REAL —
        внятная ошибка (img2img у нас пока только на Gemini).
        """
        import io

        from PIL import Image

        if self.cfg.mode is Mode.MOCK:
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            from PIL import ImageDraw

            d = ImageDraw.Draw(img)
            d.rectangle([(0, 0), (img.size[0], 40)], fill=(20, 20, 20))
            d.text((10, 10), f"EDIT: {instruction[:60]}", fill=(255, 255, 255))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

        if self.cfg.provider is not Provider.GEMINI:
            raise RuntimeError(
                "Правки картинок сейчас только на провайдере Gemini "
                f"(Nano Banana). Текущий: {self.cfg.provider.value}."
            )

        from google import genai

        client = genai.Client(api_key=self.cfg.gemini_api_key or None)
        src = Image.open(io.BytesIO(image_bytes))
        src.load()
        prompt = (
            "Apply this change to the image, keep the overall style and "
            "composition. If there is any text, keep it accurate Russian "
            f"(no gibberish). Change: {instruction}"
        )
        resp = await client.aio.models.generate_content(
            model=self.cfg.gemini_model,
            contents=[src, prompt],
        )
        return self._extract_gemini_bytes(resp)
