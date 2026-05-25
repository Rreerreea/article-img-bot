"""Telegram-слой — тонкий адаптер над PipelineService.

UX: inline-кнопки (смета→«Запустить», стиль, правка по кнопке слота),
прогресс генерации, превью-альбом + ZIP, онбординг /start + /help,
дружелюбные ошибки. Текстовые команды оставлены рабочими (совместимость).
Логика — в PipelineService (тестируется на моках). Сеть в тестах не гоняется.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from pathlib import Path

# Логи в stdout → systemd journal. Видны: journalctl -u article-img-bot -f
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger("bot")

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
)

from . import model_choices, presets
from .config import Config
from .pipeline import PipelineService
from .state import ChatState
from .whitelist import Whitelist

# translator.py оставлен в репе на случай если когда-нибудь захочется
# вернуть авто-перевод (Гоша зафиксировал 2026-05-25: пока без него,
# контроль точности перевода важнее).

ARTICLE_EXT = {".docx", ".md", ".txt"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
# Системные категории (всегда есть). Пользовательские — обычные подпапки в refs/.
SYSTEM_CATEGORIES = {"infographic", "story"}
# Старое имя оставлено для обратной совместимости — используется как
# «известные базовые». Новые категории создаются динамически.
REF_TYPES = SYSTEM_CATEGORIES
MAX_REFS = 4
# Имя категории: латиница, цифры, подчёркивания. Без пробелов и спецсимволов.
CATEGORY_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")

START_TEXT = (
    "👋 Пришли статью — сделаю картинки.\n\n"
    "Файл .docx/.md/.txt или ссылка. "
    "Отмечай места маркером «Рис.» и буллетами."
)


def _guarded(update, wl: Whitelist) -> bool:
    user = update.effective_user
    return wl.is_allowed(user.id if user else None)


async def _deny(update) -> None:
    tgt = update.message or (
        update.callback_query.message if update.callback_query else None
    )
    if tgt:
        await tgt.reply_text(
            "Нет доступа. Бот приватный — попроси владельца добавить твой ID."
        )


def _ref_count(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for f in folder.iterdir() if f.suffix.lower() in IMG_EXT)


def _estimate_kb(
    n: int, style_label: str, model_label: str
) -> InlineKeyboardMarkup:
    # «Стиль» теперь = выбор какой категории рефов использовать для генерации.
    # Старый текстовый пресет-механизм (presets.py) переименован внутренне,
    # тут оверрайдит slot.category одним выбором на всю пачку.
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"🚀 Запустить ({n})", callback_data="go")],
            [InlineKeyboardButton(
                f"🎨 Стиль: {style_label}", callback_data="style:menu"
            )],
            [
                InlineKeyboardButton(
                    f"🤖 Модель: {model_label}", callback_data="model:menu"
                ),
            ],
            [InlineKeyboardButton("Отмена", callback_data="cancel")],
        ]
    )


def _model_kb(current: str, has_gemini: bool) -> InlineKeyboardMarkup:
    rows = []
    for c in model_choices.available(has_gemini):
        mark = "✓ " if c.key == current else ""
        rows.append(
            [InlineKeyboardButton(mark + c.label, callback_data=f"model:{c.key}")]
        )
    return InlineKeyboardMarkup(rows)


def _style_kb_refs(current: str, categories: list[str]) -> InlineKeyboardMarkup:
    """Меню выбора «стиля» = категории рефов, которая будет
    использоваться для всей генерации.
    """
    rows = []
    auto_mark = "✓ " if current == "auto" else ""
    rows.append([InlineKeyboardButton(
        f"{auto_mark}🪄 Авто (по содержимому статьи)",
        callback_data="style:auto",
    )])
    for c in categories:
        mark = "✓ " if c == current else ""
        label = CATEGORY_LABELS.get(c, f"📁 {c}")
        rows.append([InlineKeyboardButton(
            mark + label, callback_data=f"style:{c}"
        )])
    return InlineKeyboardMarkup(rows)


def _edit_kb(ids: list[str], context) -> InlineKeyboardMarkup | None:
    """Кнопки правки по слотам. Длинные id мапим через user_data
    (callback_data лимит 64 байта)."""
    if not ids:
        return None
    cmap = {str(i): sid for i, sid in enumerate(ids)}
    context.user_data["edit_map"] = cmap
    row, rows = [], []
    for i, sid in cmap.items():
        row.append(
            InlineKeyboardButton(f"✏️ {sid}"[:24], callback_data=f"edit:{i}")
        )
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


CATEGORY_LABELS = {
    "infographic": "🧮 Инфографика",
    "story": "🎬 Сюжет",
}


def _category_label(name: str) -> str:
    """Подпись для категории. Системные — с иконками, кастомные — как есть."""
    return CATEGORY_LABELS.get(name, f"📁 {name}")


def _refs_kb(counts: dict[str, int]) -> InlineKeyboardMarkup:
    """Меню /refs. По строке на категорию + кнопка добавления новой."""
    rows = []
    # Системные сверху, потом кастомные алфавитно.
    ordered = ["infographic", "story"] + sorted(
        k for k in counts if k not in SYSTEM_CATEGORIES
    )
    for name in ordered:
        if name not in counts:
            continue
        rows.append([InlineKeyboardButton(
            f"{_category_label(name)} ({counts[name]})",
            callback_data=f"refs:show:{name}",
        )])
    rows.append([InlineKeyboardButton(
        "➕ Новая категория", callback_data="refs:newcat"
    )])
    rows.append([InlineKeyboardButton(
        "✅ Готово", callback_data="refs:close"
    )])
    return InlineKeyboardMarkup(rows)


def build_handlers(cfg: Config, wl: Whitelist) -> dict:
    """Возвращает dict хэндлеров (расширяемо, тестируемо)."""
    service = PipelineService(cfg)
    state = ChatState(cfg.base_dir / ".state" / "chat.json")

    def _preset(update) -> str:
        # Текстовый пресет сейчас всегда дефолт (UI скрыт).
        return presets.DEFAULT

    def _ref_style(update) -> str:
        """Текущий выбор «стиля» в чате = категория рефов или 'auto'."""
        chat = update.effective_chat
        return state.get(chat.id, "ref_style", "auto") if chat else "auto"

    def _ref_style_label(name: str) -> str:
        if name == "auto":
            return "Авто"
        return CATEGORY_LABELS.get(name, f"📁 {name}")

    def _choice_key(update) -> str:
        chat = update.effective_chat
        raw = state.get(chat.id, "model", None) if chat else None
        return model_choices.canon(raw)

    def _choice(update):
        return model_choices.get(_choice_key(update))

    has_gemini = bool(cfg.gemini_api_key)

    def _refs_dir(kind: str) -> Path:
        d = cfg.refs_dir / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _list_categories() -> dict[str, int]:
        """Все категории на диске + системные с гарантированными папками."""
        cfg.refs_dir.mkdir(parents=True, exist_ok=True)
        for s in SYSTEM_CATEGORIES:
            (cfg.refs_dir / s).mkdir(parents=True, exist_ok=True)
        return {
            d.name: _ref_count(d)
            for d in sorted(cfg.refs_dir.iterdir()) if d.is_dir()
        }

    def _category_exists(name: str) -> bool:
        return (
            name in SYSTEM_CATEGORIES
            or (cfg.refs_dir / name).is_dir()
        ) and not ("/" in name or ".." in name)

    def _categories_kb() -> InlineKeyboardMarkup:
        """Кнопки выбора категории при загрузке фото."""
        cats = list(_list_categories().keys())
        rows, row = [], []
        for c in cats:
            row.append(InlineKeyboardButton(
                _category_label(c), callback_data=f"rsave:{c}"
            ))
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([InlineKeyboardButton(
            "➕ Новая категория", callback_data="rsave:newcat"
        )])
        return InlineKeyboardMarkup(rows)

    async def _save_ref(message, kind: str, tg_file) -> None:
        folder = _refs_dir(kind)
        n = _ref_count(folder)
        if n >= MAX_REFS:
            return await message.reply_text(
                f"Уже {MAX_REFS} рефов для «{kind}» — лимит. "
                f"Удали лишние через /refs."
            )
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        await tg_file.download_to_drive(folder / f"ref_{ts}.jpg")
        await message.reply_text(
            f"Реф добавлен в «{kind}» ({n + 1}/{MAX_REFS}). "
            "Ещё — шли сюда. Список и удаление — /refs."
        )

    async def start(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        # Чистая сессия: сбрасываем running (залип после краша/OOM),
        # очередь streaming-слотов, флаги ожидания. Рефы и выбор модели
        # сохраняем — они на чат, не на сессию.
        for k in (
            "running",
            "slot_queue",
            "showing_slot",
            "gen_done",
            "result_zip_path",
            "slots",
            "slots_original",
            "awaiting_edit",
            "awaiting_new_category",
            "awaiting_category_for_photo",
            "awaiting_style_description",
        ):
            context.user_data.pop(k, None)
        await update.message.reply_text(START_TEXT)

    async def on_help(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        await update.message.reply_text(
            "Как пользоваться:\n"
            "📄 Статья → картинки: кинь файл или ссылку.\n"
            "📸 Свой реф: пришли фото — спрошу куда сохранить.\n"
            "🌍 Перевод: после ZIPа жми «Перевести» и кинь статью с другим языком.\n"
            "🤖 Модель: кнопкой при смете."
        )

    # ---- генерация (общая для кнопки и /go) ------------------------------

    async def _run_generation(update, context):
        q = update.callback_query
        chat_msg = q.message if q else update.message
        slots = context.user_data.get("slots")
        if not slots:
            txt = "Сначала пришли статью — потом запуск."
            return await (q.edit_message_text(txt) if q
                          else update.message.reply_text(txt))
        if context.user_data.get("running"):
            user_id = update.effective_user.id if update.effective_user else "?"
            log.warning(
                "running=True блокирует Go (user=%s) — возможно залипший флаг",
                user_id,
            )
            return await chat_msg.reply_text(
                "Уже генерю, подожди — пришлю, как будет готово.\n"
                "Если ничего не приходит больше 5 минут — нажми /start, "
                "это сбросит залипший флаг."
            )

        context.user_data["running"] = True
        try:
            preset = _preset(update)
            choice = _choice(update)
            # Оверрайд категории рефов (кнопка «🎨 Стиль») — только в
            # ЛОКАЛЬНОЙ копии. НЕ синкаем обратно в user_data: иначе при
            # повторных запусках/переводах оригиналы из статьи теряются
            # и стиль навсегда залипает.
            style_name = _ref_style(update)
            if style_name and style_name != "auto":
                import dataclasses
                slots = [
                    dataclasses.replace(s, category=style_name) for s in slots
                ]
            total = len(slots)
            model_name = choice.label.split(" ~")[0]

            def _fmt_eta(remaining: int) -> str:
                import math
                batches = max(1, math.ceil(remaining / max(1, cfg.concurrency)))
                sec = batches * choice.time_per_image_sec
                if sec < 90:
                    return f"~{sec} сек"
                return f"~{round(sec / 60)} мин"

            init_text = (
                f"Генерирую через {model_name}\n"
                f"{'▱' * total}  0 из {total} · {_fmt_eta(total)}"
            )
            if q:
                status = await q.edit_message_text(init_text)
            else:
                status = await update.message.reply_text(init_text)

            last = {"n": -1}

            async def progress(done, tot):
                if done != last["n"]:
                    last["n"] = done
                    bar = "▰" * done + "▱" * (tot - done)
                    remaining = max(0, tot - done)
                    suffix = (
                        f" · осталось {_fmt_eta(remaining)}"
                        if remaining else ""
                    )
                    try:
                        await status.edit_text(
                            f"Генерирую через {model_name}\n"
                            f"{bar}  {done} из {tot}{suffix}"
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.debug("progress edit failed (likely throttle): %s", exc)

            user_id = update.effective_user.id if update.effective_user else "?"
            log.info(
                "gen start user=%s slots=%d model=%s style=%s",
                user_id, total, choice.label, style_name,
            )

            # Стрим по одной с gating: пока юзер не тапнул ОК на текущей
            # картинке, следующая не показывается. Сохраняем в user_data
            # чтобы callback'и могли двигать очередь.
            context.user_data["slot_queue"] = []
            context.user_data["showing_slot"] = None
            context.user_data["gen_done"] = False
            context.user_data["chat_id"] = chat_msg.chat_id

            async def _send_one(slot_id: str, file_path: Path):
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✅ ОК", callback_data=f"slotok:{slot_id}"
                    ),
                    InlineKeyboardButton(
                        "✏️ Внести правки", callback_data=f"edit:{slot_id}"
                    ),
                ]])
                try:
                    with open(file_path, "rb") as fh:
                        await chat_msg.reply_document(
                            document=fh,
                            filename=f"{slot_id}.png",
                            caption=f"📄 {slot_id}",
                            reply_markup=kb,
                        )
                except Exception as exc:  # noqa: BLE001
                    log.warning("stream send failed for %s: %s", slot_id, exc)

            async def _slot_done(res):
                if not (res.ok and res.file_path and res.file_path.exists()):
                    return
                if context.user_data.get("showing_slot") is None:
                    context.user_data["showing_slot"] = res.slot_id
                    await _send_one(res.slot_id, res.file_path)
                else:
                    context.user_data["slot_queue"].append({
                        "slot_id": res.slot_id,
                        "file_path": str(res.file_path),
                    })

            result = await service.run(
                slots, preset=preset, progress_cb=progress,
                choice=choice, slot_done_cb=_slot_done,
            )
            log.info(
                "gen done user=%s ok=%d cached=%d failed=%d zip=%s",
                user_id, result.ok, result.from_cache, result.failed,
                bool(result.zip_path),
            )
            for r in result.results:
                if r.error:
                    log.warning("slot %s failed: %s", r.slot_id, r.error)

            if result.zip_path is None:
                context.user_data["gen_done"] = True
                return await status.edit_text(
                    "😕 Ни одной картинки не вышло. " + result.human()
                    + "\nПопробуй другую статью или /start."
                )
            await status.edit_text(f"✅ Готово! {result.human()}")
            context.user_data["gen_done"] = True
            context.user_data["result_zip_path"] = str(result.zip_path)
            # Если юзер уже всё протапал — сразу шлём ZIP + translate.
            # Иначе финал отправит slotok-callback после последнего ОК.
            if (
                context.user_data.get("showing_slot") is None
                and not context.user_data.get("slot_queue")
            ):
                await _send_final_artifacts(chat_msg, context)
        finally:
            context.user_data["running"] = False

    async def _send_final_artifacts(chat_msg, context):
        """ZIP + кнопка перевода. Зовётся когда все слоты протапаны юзером
        и генерация завершена. Чистим streaming-state чтобы следующая
        пачка стартовала с чистой очередью."""
        zip_path = context.user_data.pop("result_zip_path", None)
        context.user_data.pop("slot_queue", None)
        context.user_data.pop("showing_slot", None)
        context.user_data.pop("gen_done", None)
        if zip_path:
            try:
                with open(zip_path, "rb") as zf:
                    await chat_msg.reply_document(
                        document=zf,
                        filename="images.zip",
                        caption="📦 Все картинки одним архивом.",
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("zip send failed: %s", exc)
        try:
            await chat_msg.reply_text(
                "Хочешь версию на другом языке?",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(
                        "🌍 Перевести", callback_data="translate"
                    )]]
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("final translate button failed: %s", exc)

    async def _advance_queue(chat_msg, context):
        """После ОК или edit — двигаем очередь дальше."""
        queue = context.user_data.get("slot_queue") or []
        if queue:
            nxt = queue.pop(0)
            context.user_data["slot_queue"] = queue
            context.user_data["showing_slot"] = nxt["slot_id"]
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "✅ ОК", callback_data=f"slotok:{nxt['slot_id']}"
                ),
                InlineKeyboardButton(
                    "✏️ Внести правки", callback_data=f"edit:{nxt['slot_id']}"
                ),
            ]])
            try:
                with open(nxt["file_path"], "rb") as fh:
                    await chat_msg.reply_document(
                        document=fh,
                        filename=f"{nxt['slot_id']}.png",
                        caption=f"📄 {nxt['slot_id']}",
                        reply_markup=kb,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("advance_queue send failed: %s", exc)
                # Пропускаем поломанный и пробуем следующий.
                context.user_data["showing_slot"] = None
                await _advance_queue(chat_msg, context)
            return
        # Очередь пуста.
        context.user_data["showing_slot"] = None
        if context.user_data.get("gen_done"):
            await _send_final_artifacts(chat_msg, context)
        else:
            # Генерация ещё идёт — иначе юзер думает что бот завис.
            try:
                await chat_msg.reply_text(
                    "⏳ Жду следующую картинку — модель ещё рисует..."
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("wait notice send failed: %s", exc)

    async def on_go(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        await _run_generation(update, context)

    # ---- правка (общая для кнопки и /edit) -------------------------------

    async def _do_edit(update, context, message, slot_id: str, instruction: str):
        choice = _choice(update)
        preset = _preset(update)
        slots = context.user_data.get("slots") or []
        slot = next((s for s in slots if s.id == slot_id), None)
        mode_hint = (
            "правлю композицию"
            if choice.supports_edit else
            "перегенерирую с твоей правкой"
        )
        sec = choice.time_per_image_sec
        eta = f"~{sec} сек" if sec < 90 else f"~{round(sec / 60)} мин"
        status = await message.reply_text(
            f"«{slot_id}» — {mode_hint} · {eta}\n▱  0 из 1"
        )
        try:
            path = await service.edit(
                slot_id, instruction,
                choice=choice, slot=slot, preset=preset,
            )
        except Exception as exc:  # noqa: BLE001 — дружелюбное сообщение
            return await status.edit_text(
                f"Не получилось поправить ({type(exc).__name__}). "
                "Попробуй другую формулировку."
            )
        if path is None:
            return await status.edit_text(
                f"Не могу найти картинку «{slot_id}» или контекст слота. "
                "Сгенерируй пачку заново."
            )
        await status.edit_text(f"«{slot_id}» готов: ▰  1 из 1")
        with open(path, "rb") as fh:
            await message.reply_document(
                document=fh,
                filename=f"{slot_id}.png",
                caption=f"Готово: {slot_id} — {instruction}",
            )

    def _clear_awaiting(context, *, keep: str | None = None) -> None:
        """Сбрасывает все awaiting_* флаги кроме `keep`. Защищает от
        ситуации когда юзер тапает разные «жди текст»-кнопки подряд:
        старые флаги могли съесть следующее сообщение."""
        for key in (
            "awaiting_edit",
            "awaiting_new_category",
            "awaiting_category_for_photo",
            "awaiting_style_description",
        ):
            if key != keep:
                context.user_data.pop(key, None)

    async def on_article(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        msg = update.message

        # Ждём текст правки (после кнопки «✏️ слот»)?
        awaiting = context.user_data.get("awaiting_edit")
        if awaiting and msg.text and not msg.text.startswith("/"):
            _clear_awaiting(context)
            return await _do_edit(update, context, msg, awaiting, msg.text.strip())

        # Ждём имя новой категории (после кнопок «➕ Новая категория»)?
        if msg.text and not msg.text.startswith("/") and (
            context.user_data.get("awaiting_new_category")
            or context.user_data.get("awaiting_category_for_photo")
        ):
            name = msg.text.strip().lower()
            if not CATEGORY_NAME_RE.match(name):
                # Не чистим awaiting_* — даём юзеру шанс ввести валидное
                # имя без потери pending-фото / нового пути.
                return await msg.reply_text(
                    "Имя не подходит. Только латиница, цифры и `_`, "
                    "начинать с буквы, до 30 символов. Попробуй ещё раз."
                )
            if name in SYSTEM_CATEGORIES:
                return await msg.reply_text(
                    f"«{name}» — системная категория, уже есть."
                )
            # Только тут — после успешной валидации — сбрасываем флаги.
            _clear_awaiting(context, keep="awaiting_style_description")
            (cfg.refs_dir / name).mkdir(parents=True, exist_ok=True)
            context.user_data["awaiting_style_description"] = name
            return await msg.reply_text(
                f"✅ Категория «{name}» создана.\n\n"
                "Опиши стиль текстом — что должно быть в картинках "
                "(цвета, настроение, композиция, подход к иллюстрации).\n"
                "Например: «тёмный фиолетовый фон, неон, изометрия, "
                "3D премиум, минимум деталей».\n\n"
                "Это добавится в промпт вместе с рефами — даст модели "
                "двойной сигнал и стиль будет точнее.\n\n"
                "Если без описания — напиши `-`."
            )

        # Ждём описание стиля (после создания новой категории).
        desc_target = context.user_data.get("awaiting_style_description")
        if desc_target and msg.text and not msg.text.startswith("/"):
            text = msg.text.strip()
            if text == "-":
                _clear_awaiting(context)
                return await msg.reply_text(
                    f"Окей, без описания. Теперь пришли фото — спрошу куда "
                    "сохранить. Список — /refs."
                )
            desc_path = cfg.refs_dir / desc_target / ".style.txt"
            try:
                desc_path.parent.mkdir(parents=True, exist_ok=True)
                desc_path.write_text(text, encoding="utf-8")
            except OSError as exc:
                log.exception("style desc write failed for %s", desc_target)
                # Флаг не чистим — даём шанс повторить ввод.
                return await msg.reply_text(
                    f"Не получилось сохранить описание ({type(exc).__name__}): "
                    f"{str(exc)[:120]}\nПопробуй ещё раз."
                )
            _clear_awaiting(context)
            return await msg.reply_text(
                f"✅ Описание сохранено для «{desc_target}».\n"
                "Теперь пришли фото-рефы — спрошу куда сохранить."
            )

        choice = _choice(update)
        try:
            if msg.document:
                doc = msg.document
                suffix = Path(doc.file_name or "").suffix.lower()
                pending = state.get(update.effective_chat.id, "pending_ref")
                if suffix in IMG_EXT and pending in REF_TYPES:
                    return await _save_ref(msg, pending, await doc.get_file())
                if suffix in IMG_EXT:
                    # Картинка прислана как ФАЙЛ (не фото) — спросим
                    # категорию так же, как для photo-сообщений.
                    return await msg.reply_text(
                        "Куда добавить этот референс?",
                        reply_markup=_categories_kb(),
                        reply_to_message_id=msg.message_id,
                    )
                if suffix not in ARTICLE_EXT:
                    return await msg.reply_text(
                        "Я понимаю .docx, .md, .txt или ссылку на статью 🙂"
                    )
                tg_file = await doc.get_file()
                tmp = Path(tempfile.gettempdir()) / doc.file_name
                await tg_file.download_to_drive(tmp)
                slots, est = service.prepare(
                    tmp, preset=_preset(update), choice=choice
                )
            else:
                txt = (msg.text or "").strip()
                if txt.startswith("http://") or txt.startswith("https://"):
                    slots, est = service.prepare(
                        txt, preset=_preset(update), choice=choice
                    )
                else:
                    slots, est = service.prepare(
                        text=txt, preset=_preset(update), choice=choice
                    )
        except Exception as exc:  # noqa: BLE001 — дружелюбно, не стектрейс
            log.exception("on_article: failed to load/parse")
            return await msg.reply_text(
                "Не смог обработать это 😕 "
                f"{type(exc).__name__}: {str(exc)[:200]}\n"
                "Пришли статью файлом или ссылкой."
            )

        if not slots:
            return await msg.reply_text(
                "Не нашёл мест под картинки (маркер «Рис.»). "
                "Проверь статью или пришли другую."
            )

        context.user_data["slots"] = slots
        # Бэкап оригинальных слотов для translate-flow: чтобы повторный
        # перевод (RU→EN→RU и т.п.) шёл из исходного текста статьи, а
        # не из уже-переведённого user_data["slots"].
        context.user_data["slots_original"] = slots
        style_name = _ref_style(update)
        style_label = _ref_style_label(style_name)
        m_label = choice.label
        await msg.reply_text(
            f"{est.human()}\n🎨 Стиль: {style_label}\n🤖 Модель: {m_label}",
            reply_markup=_estimate_kb(len(slots), style_label, m_label),
        )

    async def on_style(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        cats = list(_list_categories().keys())
        await update.message.reply_text(
            "Какую категорию рефов использовать для всей пачки?",
            reply_markup=_style_kb_refs(_ref_style(update), cats),
        )

    def _list_refs(kind: str) -> list[Path]:
        folder = _refs_dir(kind)
        return sorted(
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() in IMG_EXT
        )

    async def on_refs(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        counts = _list_categories()
        lines = ["📸 Твои рефы:"]
        for name in (
            ["infographic", "story"]
            + sorted(k for k in counts if k not in SYSTEM_CATEGORIES)
        ):
            if name in counts:
                lines.append(
                    f"• {_category_label(name)}: {counts[name]}"
                )
        lines.append("")
        lines.append("Тапни категорию — покажу рефы с кнопкой удаления.")
        lines.append("Чтобы добавить реф — пришли мне фото.")
        lines.append("В статье используй маркер `Рис.[категория] Заголовок` "
                     "чтобы привязать слот к конкретной категории.")
        await update.message.reply_text(
            "\n".join(lines), reply_markup=_refs_kb(counts)
        )

    async def _show_refs_list(q, kind: str) -> None:
        files = _list_refs(kind)
        label = _category_label(kind)
        back_kb = InlineKeyboardMarkup([[InlineKeyboardButton(
            "« Назад к рефам", callback_data="refs:back"
        )]])
        if not files:
            return await q.message.reply_text(
                f"{label}: пусто. Пришли фото — спрошу куда сохранить.",
                reply_markup=back_kb,
            )
        await q.message.reply_text(
            f"Все рефы {label} ({len(files)} шт):"
        )
        for i, f in enumerate(files, 1):
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton(
                    "🗑 Удалить", callback_data=f"refs:del:{kind}:{f.name}"
                )]]
            )
            with open(f, "rb") as fh:
                await q.message.reply_photo(
                    photo=fh, caption=f"#{i} · {f.name}", reply_markup=kb
                )
        await q.message.reply_text(
            "Это всё. Вернуться к категориям?", reply_markup=back_kb
        )

    async def on_ref_photo(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        pending = state.get(update.effective_chat.id, "pending_ref")
        if pending and _category_exists(pending):
            photo = update.message.photo[-1]
            return await _save_ref(
                update.message, pending, await photo.get_file()
            )
        await update.message.reply_text(
            "Куда добавить этот референс?",
            reply_markup=_categories_kb(),
            reply_to_message_id=update.message.message_id,
        )

    async def on_edit(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        args = context.args or []
        ids = service.available_slot_ids()
        if len(args) < 2:
            kb = _edit_kb(ids, context)
            return await update.message.reply_text(
                "Выбери картинку для правки:" if kb
                else "Сначала сгенерируй картинки.",
                reply_markup=kb,
            )
        await _do_edit(update, context, update.message, args[0], " ".join(args[1:]))

    async def on_callback(update, context):
        q = update.callback_query
        await q.answer()
        if not _guarded(update, wl):
            return await _deny(update)
        data = q.data or ""

        if data == "go":
            return await _run_generation(update, context)
        if data == "cancel":
            context.user_data.pop("slots", None)
            return await q.edit_message_text(
                "Отменено. Пришли статью заново, когда будешь готов."
            )
        if data == "style:menu":
            cats = list(_list_categories().keys())
            return await q.edit_message_text(
                "Какую категорию рефов использовать для всей пачки?",
                reply_markup=_style_kb_refs(_ref_style(update), cats),
            )
        if data.startswith("style:"):
            raw = data.split(":", 1)[1]
            # «auto» — спец-значение, иначе должна быть существующая категория.
            if raw != "auto" and not _category_exists(raw):
                return await q.edit_message_text(
                    "Этой категории уже нет. Попробуй ещё раз через /refs."
                )
            state.set(update.effective_chat.id, "ref_style", raw)
            style_label = _ref_style_label(raw)
            n = len(context.user_data.get("slots") or [])
            m_label = _choice(update).label
            if n:
                return await q.edit_message_text(
                    f"🎨 Стиль: {style_label}\n🤖 Модель: {m_label}",
                    reply_markup=_estimate_kb(n, style_label, m_label),
                )
            return await q.edit_message_text(
                f"Стиль: {style_label}. Пришли статью — посчитаю смету."
            )
        if data == "model:menu":
            return await q.edit_message_text(
                "Выбери модель:",
                reply_markup=_model_kb(_choice_key(update), has_gemini),
            )
        if data.startswith("model:"):
            key = model_choices.canon(data.split(":", 1)[1])
            state.set(update.effective_chat.id, "model", key)
            choice_obj = model_choices.get(key)
            n = len(context.user_data.get("slots") or [])
            style_label = _ref_style_label(_ref_style(update))
            if n:
                return await q.edit_message_text(
                    f"🎨 Стиль: {style_label}\n🤖 Модель: {choice_obj.label}",
                    reply_markup=_estimate_kb(n, style_label, choice_obj.label),
                )
            return await q.edit_message_text(
                f"Модель: {choice_obj.label}. Пришли статью — посчитаю смету."
            )
        if data.startswith("slotok:"):
            sid = data.split(":", 1)[1]
            # Снимаем кнопки у текущего файла (визуально «принято»).
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
            if context.user_data.get("showing_slot") == sid:
                await _advance_queue(q.message, context)
            return
        if data.startswith("edit:"):
            sid = data.split(":", 1)[1]
            # Проверяем что слот вообще существует на диске.
            if not (cfg.output_dir / f"{sid}.png").exists():
                return await q.message.reply_text(
                    f"Картинка «{sid}» не найдена — сгенерируй заново."
                )
            _clear_awaiting(context, keep="awaiting_edit")
            context.user_data["awaiting_edit"] = sid
            # Снимаем кнопки у этого слота и двигаем очередь — edit пойдёт
            # параллельно, не блокируя поток картинок.
            try:
                await q.edit_message_reply_markup(reply_markup=None)
            except Exception:  # noqa: BLE001
                pass
            if context.user_data.get("showing_slot") == sid:
                await _advance_queue(q.message, context)
            return await q.message.reply_text(
                f"Что изменить в «{sid}»? Напиши текстом "
                "(например: сделай фон темнее, убери иконку)."
            )
        if data == "rsave:newcat":
            # Запоминаем фото (по reply_to) и просим имя новой категории.
            parent = q.message.reply_to_message
            if not parent:
                return await q.edit_message_text(
                    "Фото потерялось 😕 Пришли его заново."
                )
            _clear_awaiting(context, keep="awaiting_category_for_photo")
            context.user_data["awaiting_category_for_photo"] = parent.message_id
            return await q.edit_message_text(
                "Как назвать категорию? Напиши одним словом латиницей "
                "(маленькие буквы, цифры, подчёркивания). Например: "
                "characters, charts, screenshots."
            )
        if data.startswith("rsave:"):
            kind = data.split(":", 1)[1]
            if not _category_exists(kind):
                return await q.edit_message_text(
                    "Неизвестная категория, пришли фото заново."
                )
            parent = q.message.reply_to_message
            if not parent:
                return await q.edit_message_text(
                    "Фото потерялось 😕 Пришли его заново."
                )
            # Картинка могла прийти и как фото, и как файл-документ.
            if parent.photo:
                tg_file = await parent.photo[-1].get_file()
            elif parent.document and Path(
                parent.document.file_name or ""
            ).suffix.lower() in IMG_EXT:
                tg_file = await parent.document.get_file()
            else:
                return await q.edit_message_text(
                    "Фото потерялось 😕 Пришли его заново."
                )
            folder = _refs_dir(kind)
            n = _ref_count(folder)
            if n >= MAX_REFS:
                return await q.edit_message_text(
                    f"Уже {MAX_REFS} рефов для «{kind}» — это максимум "
                    f"для генерации. Удали лишние через /refs."
                )
            import datetime as _dt
            ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            await tg_file.download_to_drive(folder / f"ref_{ts}.jpg")
            label = "инфографику" if kind == "infographic" else "сюжетные"
            return await q.edit_message_text(
                f"Сохранено в {label} ({n + 1}/{MAX_REFS}). "
                "Шли ещё фото — спрошу куда. Список — /refs."
            )
        if data == "translate":
            # Простой флоу: «пришли переведённую статью файлом» → бот
            # перерисует картинки с новым текстом. Без автоперевода —
            # автор сам контролирует точность.
            context.user_data.pop("slots", None)
            _clear_awaiting(context)
            return await q.message.reply_text(
                "🌍 Пришли статью с переведённым текстом — в том же формате "
                "(Рис. + заголовок + буллеты). Я перерисую те же картинки "
                "с новым текстом."
            )
        if data == "refs:back":
            counts = _list_categories()
            lines = ["📸 Твои рефы:"]
            for name in (
                ["infographic", "story"]
                + sorted(k for k in counts if k not in SYSTEM_CATEGORIES)
            ):
                if name in counts:
                    lines.append(f"• {_category_label(name)}: {counts[name]}")
            lines.append("")
            lines.append("Тапни категорию — покажу рефы с удалением.")
            return await q.message.reply_text(
                "\n".join(lines), reply_markup=_refs_kb(counts)
            )
        if data == "refs:close":
            return await q.edit_message_text("Окей, закрыл.")
        if data == "refs:newcat":
            _clear_awaiting(context, keep="awaiting_new_category")
            context.user_data["awaiting_new_category"] = True
            return await q.edit_message_text(
                "Как назвать категорию? Напиши одним словом латиницей "
                "(маленькие буквы, цифры, подчёркивания). Например: "
                "characters, charts, screenshots.\n\n"
                "В статье потом будешь писать `Рис.[название] Заголовок`, "
                "чтобы привязать слот к этой категории."
            )
        if data.startswith("refs:show:"):
            kind = data.split(":", 2)[2]
            if not _category_exists(kind):
                return
            return await _show_refs_list(q, kind)
        if data.startswith("refs:del:"):
            parts = data.split(":", 3)
            if len(parts) < 4:
                return
            kind, filename = parts[2], parts[3]
            if not _category_exists(kind):
                return await q.edit_message_caption(caption="Неизвестная категория.")
            if "/" in filename or ".." in filename:
                return await q.edit_message_caption(caption="Подозрительное имя файла.")
            target = _refs_dir(kind) / filename
            async def _ack_delete(text: str) -> None:
                # Старые сообщения нельзя edit_message_caption (>48h
                # лимит Telegram). Падаем в reply_text — UX ок,
                # действие подтверждается.
                try:
                    await q.edit_message_caption(caption=text)
                except Exception:  # noqa: BLE001
                    try:
                        await q.message.reply_text(text)
                    except Exception:  # noqa: BLE001
                        log.warning("refs:del ack failed for %s", filename)
            if not target.exists():
                return await _ack_delete(f"❎ {filename} — уже нет.")
            target.unlink()
            return await _ack_delete(f"🗑 Удалено: {filename}")

    async def on_error(update, context):
        err = getattr(context, "error", None)
        log.exception("Unhandled bot error: %s", err, exc_info=err)
        tgt = getattr(update, "effective_message", None)
        if tgt:
            try:
                msg = type(err).__name__ if err else "unknown"
                detail = str(err)[:120] if err else ""
                await tgt.reply_text(
                    f"Что-то пошло не так ({msg}). {detail}\n"
                    "Попробуй ещё раз или /start."
                )
            except Exception:  # noqa: BLE001
                log.exception("on_error: failed to notify user")

    return {
        "start": start, "help": on_help, "go": on_go, "article": on_article,
        "style": on_style, "refs": on_refs, "ref_photo": on_ref_photo,
        "edit": on_edit, "callback": on_callback, "error": on_error,
    }


async def _post_init(app):  # pragma: no cover — сетевое
    from telegram import BotCommand

    # СБРОС залипших running-флагов после краша/OOM-killa: если процесс
    # был убит во время генерации, finally не отработал и user_data[running]
    # остался True в pickle — юзер навечно лочится. Чистим у всех.
    cleared = 0
    for chat_id, data in list(app.user_data.items()):
        if data.pop("running", None):
            cleared += 1
    if cleared:
        log.warning("Сбросил залипший running у %d чатов на старте", cleared)

    await app.bot.set_my_commands(
        [
            BotCommand("start", "начать / как пользоваться"),
            BotCommand("help", "помощь"),
            BotCommand("refs", "свои образцы стиля"),
        ]
    )


def main() -> None:  # pragma: no cover — сетевой запуск, проверяется живьём
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        PicklePersistence,
        filters,
    )

    token = os.getenv("TELEGRAM_TOKEN", "")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN пуст — нечем поднимать бота.")

    cfg = Config.from_env()
    wl = Whitelist.from_env()
    if wl.is_empty:
        raise RuntimeError(
            "HF_ALLOWED_USER_IDS пуст: бот никого не пустит. Заполни "
            "список ID или поставь '*' для теста."
        )

    h = build_handlers(cfg, wl)
    state_dir = cfg.base_dir / ".state"
    state_dir.mkdir(parents=True, exist_ok=True)
    persistence = PicklePersistence(filepath=state_dir / "bot.pickle")
    app = (
        Application.builder()
        .token(token)
        .persistence(persistence)
        .post_init(_post_init)
        .build()
    )
    app.add_handler(CommandHandler("start", h["start"]))
    app.add_handler(CommandHandler("help", h["help"]))
    app.add_handler(CommandHandler("go", h["go"]))
    app.add_handler(CommandHandler("style", h["style"]))
    app.add_handler(CommandHandler("refs", h["refs"]))
    app.add_handler(CommandHandler("edit", h["edit"]))
    app.add_handler(CallbackQueryHandler(h["callback"]))
    app.add_handler(MessageHandler(filters.PHOTO, h["ref_photo"]))
    app.add_handler(
        MessageHandler(
            filters.Document.ALL | filters.TEXT & ~filters.COMMAND,
            h["article"],
        )
    )
    app.add_error_handler(h["error"])
    app.run_polling()


if __name__ == "__main__":  # pragma: no cover
    main()
