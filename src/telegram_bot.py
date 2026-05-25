"""Telegram-слой — тонкий адаптер над PipelineService.

UX: inline-кнопки (смета→«Запустить», стиль, правка по кнопке слота),
прогресс генерации, превью-альбом + ZIP, онбординг /start + /help,
дружелюбные ошибки. Текстовые команды оставлены рабочими (совместимость).
Логика — в PipelineService (тестируется на моках). Сеть в тестах не гоняется.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

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

ARTICLE_EXT = {".docx", ".md", ".txt"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
REF_TYPES = {"infographic", "story"}
MAX_REFS = 8

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
    n: int, preset_label: str, model_label: str
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"🚀 Запустить ({n})", callback_data="go")],
            [
                InlineKeyboardButton(
                    f"🎨 Стиль: {preset_label}", callback_data="style:menu"
                ),
            ],
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


def _style_kb(current: str) -> InlineKeyboardMarkup:
    rows = []
    for name, p in presets.PRESETS.items():
        mark = "✓ " if name == current else ""
        rows.append(
            [InlineKeyboardButton(mark + p.label, callback_data=f"style:{name}")]
        )
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


def _refs_kb(ig_count: int, st_count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"🧮 Инфографика ({ig_count})",
                    callback_data="refs:show:infographic",
                ),
                InlineKeyboardButton(
                    f"🎬 Сюжет ({st_count})",
                    callback_data="refs:show:story",
                ),
            ],
        ]
    )


def build_handlers(cfg: Config, wl: Whitelist) -> dict:
    """Возвращает dict хэндлеров (расширяемо, тестируемо)."""
    service = PipelineService(cfg)
    state = ChatState(cfg.base_dir / ".state" / "chat.json")

    def _preset(update) -> str:
        chat = update.effective_chat
        return presets.canon(
            state.get(chat.id, "preset", presets.DEFAULT) if chat else None
        )

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
        await update.message.reply_text(START_TEXT)

    async def on_help(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        await update.message.reply_text(
            "Как пользоваться:\n"
            "📄 Статья → картинки: кинь файл или ссылку.\n"
            "📸 Свой реф: пришли фото — спрошу куда сохранить.\n"
            "🌍 Перевод: после ZIPа жми «Перевести» и кинь статью с другим языком.\n"
            "🤖 Модель/стиль: кнопками при смете."
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
            return await chat_msg.reply_text(
                "Уже генерю, подожди — пришлю, как будет готово."
            )

        context.user_data["running"] = True
        try:
            preset = _preset(update)
            choice = _choice(update)
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
                    except Exception:  # noqa: BLE001 — троттлинг Telegram
                        pass

            result = await service.run(slots, preset=preset,
                                       progress_cb=progress, choice=choice)

            if result.zip_path is None:
                return await status.edit_text(
                    "😕 Ни одной картинки не вышло. " + result.human()
                    + "\nПопробуй другую статью или /start."
                )
            await status.edit_text(f"✅ Готово! {result.human()}")

            ok = [r for r in result.results if r.ok and r.file_path]
            # Превью альбомом (сжато, быстро глянуть в чате), по 10.
            for i in range(0, len(ok), 10):
                chunk = ok[i:i + 10]
                media = [
                    InputMediaPhoto(open(r.file_path, "rb")) for r in chunk
                ]
                if media:
                    try:
                        await chat_msg.reply_media_group(media=media)
                    except Exception:  # noqa: BLE001 — не критично, есть ZIP
                        pass
            # Архив документом — оригиналы без сжатия.
            await chat_msg.reply_document(
                document=open(result.zip_path, "rb"),
                filename="images.zip",
                caption="📦 Оригиналы без сжатия — в архиве.",
            )
            ids = [r.slot_id for r in ok]
            kb = _edit_kb(ids, context)
            if kb:
                hint = (
                    "Поправить картинку? Выбери:"
                    if choice.supports_edit else
                    "Поправить картинку? Выбери (перегенерация ~"
                    f"${choice.price_per_image:.2f}/шт):"
                )
                await chat_msg.reply_text(hint, reply_markup=kb)
            await chat_msg.reply_text(
                "Хочешь версию на другом языке?",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(
                        "🌍 Перевести", callback_data="translate"
                    )]]
                ),
            )
        finally:
            context.user_data["running"] = False

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
        await message.reply_document(
            document=open(path, "rb"),
            filename=f"{slot_id}.png",
            caption=f"Готово: {slot_id} — {instruction}",
        )

    async def on_article(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        msg = update.message

        # Ждём текст правки (после кнопки «✏️ слот»)?
        awaiting = context.user_data.get("awaiting_edit")
        if awaiting and msg.text and not msg.text.startswith("/"):
            context.user_data["awaiting_edit"] = None
            return await _do_edit(update, context, msg, awaiting, msg.text.strip())

        choice = _choice(update)
        try:
            if msg.document:
                doc = msg.document
                suffix = Path(doc.file_name or "").suffix.lower()
                pending = state.get(update.effective_chat.id, "pending_ref")
                if suffix in IMG_EXT and pending in REF_TYPES:
                    return await _save_ref(msg, pending, await doc.get_file())
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
            return await msg.reply_text(
                "Не смог обработать это 😕 Причина: "
                f"{type(exc).__name__}. Пришли статью файлом или ссылкой."
            )

        if not slots:
            return await msg.reply_text(
                "Не нашёл мест под картинки (маркер «Рис.»). "
                "Проверь статью или пришли другую."
            )

        context.user_data["slots"] = slots
        label = presets.get(_preset(update)).label
        m_label = choice.label
        await msg.reply_text(
            f"{est.human()}\n🎨 Стиль: {label}\n🤖 Модель: {m_label}",
            reply_markup=_estimate_kb(len(slots), label, m_label),
        )

    async def on_style(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        chat = update.effective_chat
        arg = context.args[0].strip().lower() if context.args else ""
        if arg in presets.PRESETS:
            state.set(chat.id, "preset", arg)
            return await update.message.reply_text(
                f"Стиль: {presets.PRESETS[arg].label}. "
                "Действует со следующей генерации."
            )
        await update.message.reply_text(
            "Выбери стиль:", reply_markup=_style_kb(_preset(update))
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
        ig = _ref_count(cfg.refs_dir / "infographic")
        st = _ref_count(cfg.refs_dir / "story")
        await update.message.reply_text(
            f"📸 Твои рефы:\n"
            f"• Инфографика: {ig}\n"
            f"• Сюжет: {st}\n\n"
            "Тапни категорию — покажу каждый реф с кнопкой удаления.\n"
            "Чтобы добавить — просто пришли мне фото.",
            reply_markup=_refs_kb(ig, st),
        )

    async def _show_refs_list(q, kind: str) -> None:
        files = _list_refs(kind)
        label = "инфографики" if kind == "infographic" else "сюжета"
        if not files:
            return await q.message.reply_text(
                f"Пусто. Пришли фото — спрошу куда сохранить."
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

    async def on_ref_photo(update, context):
        if not _guarded(update, wl):
            return await _deny(update)
        pending = state.get(update.effective_chat.id, "pending_ref")
        if pending in REF_TYPES:
            photo = update.message.photo[-1]
            return await _save_ref(
                update.message, pending, await photo.get_file()
            )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🧮 Инфографика", callback_data="rsave:infographic"
                    ),
                    InlineKeyboardButton(
                        "🎬 Сюжет", callback_data="rsave:story"
                    ),
                ]
            ]
        )
        await update.message.reply_text(
            "Куда добавить этот референс?",
            reply_markup=kb,
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
            return await q.edit_message_text(
                "Выбери стиль:", reply_markup=_style_kb(_preset(update))
            )
        if data.startswith("style:"):
            name = presets.canon(data.split(":", 1)[1])
            state.set(update.effective_chat.id, "preset", name)
            n = len(context.user_data.get("slots") or [])
            label = presets.PRESETS[name].label
            m_label = _choice(update).label
            if n:
                return await q.edit_message_text(
                    f"🎨 Стиль: {label}\n🤖 Модель: {m_label}",
                    reply_markup=_estimate_kb(n, label, m_label),
                )
            return await q.edit_message_text(
                f"Стиль: {label}. Пришли статью — посчитаю смету."
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
            label = presets.get(_preset(update)).label
            if n:
                return await q.edit_message_text(
                    f"🎨 Стиль: {label}\n🤖 Модель: {choice_obj.label}",
                    reply_markup=_estimate_kb(n, label, choice_obj.label),
                )
            return await q.edit_message_text(
                f"Модель: {choice_obj.label}. Пришли статью — посчитаю смету."
            )
        if data.startswith("edit:"):
            idx = data.split(":", 1)[1]
            sid = (context.user_data.get("edit_map") or {}).get(idx)
            if not sid:
                return await q.edit_message_text(
                    "Эта картинка уже неактуальна — сгенерируй заново."
                )
            context.user_data["awaiting_edit"] = sid
            return await q.message.reply_text(
                f"Что изменить в «{sid}»? Напиши текстом "
                "(например: сделай фон темнее, убери иконку)."
            )
        if data.startswith("rsave:"):
            kind = data.split(":", 1)[1]
            if kind not in REF_TYPES:
                return await q.edit_message_text(
                    "Неизвестная категория, пришли фото заново."
                )
            parent = q.message.reply_to_message
            if not parent or not parent.photo:
                return await q.edit_message_text(
                    "Фото потерялось 😕 Пришли его заново."
                )
            photo = parent.photo[-1]
            folder = _refs_dir(kind)
            n = _ref_count(folder)
            if n >= MAX_REFS:
                return await q.edit_message_text(
                    f"Уже {MAX_REFS} рефов для «{kind}» — лимит. "
                    f"Удали лишние через /refs."
                )
            import datetime as _dt
            ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            tg_file = await photo.get_file()
            await tg_file.download_to_drive(folder / f"ref_{ts}.jpg")
            label = "инфографику" if kind == "infographic" else "сюжетные"
            return await q.edit_message_text(
                f"Сохранено в {label} ({n + 1}/{MAX_REFS}). "
                "Шли ещё фото — спрошу куда. Список — /refs."
            )
        if data == "translate":
            context.user_data.pop("slots", None)
            return await q.message.reply_text(
                "🌍 Пришли статью с переведённым текстом — в том же формате "
                "(Рис. + заголовок + буллеты). Я перерисую те же картинки "
                "с новым текстом."
            )
        if data.startswith("refs:show:"):
            kind = data.split(":", 2)[2]
            if kind not in REF_TYPES:
                return
            return await _show_refs_list(q, kind)
        if data.startswith("refs:del:"):
            parts = data.split(":", 3)
            if len(parts) < 4:
                return
            kind, filename = parts[2], parts[3]
            if kind not in REF_TYPES:
                return await q.edit_message_caption(caption="Неизвестная категория.")
            # Защита от path traversal — имя без слешей.
            if "/" in filename or ".." in filename:
                return await q.edit_message_caption(caption="Подозрительное имя файла.")
            target = _refs_dir(kind) / filename
            if not target.exists():
                return await q.edit_message_caption(
                    caption=f"❎ {filename} — уже нет."
                )
            target.unlink()
            return await q.edit_message_caption(
                caption=f"🗑 Удалено: {filename}"
            )

    async def on_error(update, context):
        tgt = getattr(update, "effective_message", None)
        if tgt:
            try:
                await tgt.reply_text(
                    "Что-то пошло не так 😕 Попробуй ещё раз или /start."
                )
            except Exception:  # noqa: BLE001
                pass

    return {
        "start": start, "help": on_help, "go": on_go, "article": on_article,
        "style": on_style, "refs": on_refs, "ref_photo": on_ref_photo,
        "edit": on_edit, "callback": on_callback, "error": on_error,
    }


async def _post_init(app):  # pragma: no cover — сетевое
    from telegram import BotCommand

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
