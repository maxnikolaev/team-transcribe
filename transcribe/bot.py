"""Telegram-бот: принимает аудио + комментарий, транскрибирует, сохраняет в git."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

from .config import get_settings
from .deepgram_client import format_transcript, transcribe as transcribe_audio
from .git_sync import commit_and_push
from .project_router import (accessible_projects, can_create_projects,
                             create_project, extract_topic, find_project,
                             sender_name)
from .storage import save_transcript

log = logging.getLogger("team-transcribe")

router = Router()

# Ожидание выбора проекта: tg_id -> {"audio_path", "topic", "awaiting_new_name"}
_pending: dict[int, dict] = {}


def _allowed(tg_id: int) -> bool:
    return get_settings().user(tg_id) is not None


def _projects_keyboard(settings, tg_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=p.name, callback_data=f"proj:{p.id}")]
        for p in accessible_projects(settings, tg_id)
    ]
    if can_create_projects(settings, tg_id):
        rows.append([InlineKeyboardButton(text="➕ Новый проект", callback_data="proj:__new__")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="proj:__cancel__")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _ext_for(message: Message) -> str:
    if message.voice:
        return ".ogg"
    if message.audio:
        name = message.audio.file_name or ""
        if name and Path(name).suffix:
            return Path(name).suffix.lower()
        mime_to_ext = {
            "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
            "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
            "audio/flac": ".flac", "audio/aac": ".aac", "audio/webm": ".webm",
        }
        return mime_to_ext.get(message.audio.mime_type or "", ".mp3")
    return ".bin"


async def _download(bot: Bot, file_id: str, dest: Path) -> Path:
    f = await bot.get_file(file_id)
    await bot.download_file(f.file_path, destination=dest)
    return dest


async def _process(bot: Bot, tg_id: int, audio_path: Path, project, topic: str) -> None:
    settings = get_settings()
    name = sender_name(settings, tg_id)

    status = await bot.send_message(tg_id, f"⏳ Транскрибирую… ({project.name})")

    # 1) Транскрибация
    try:
        result = await asyncio.to_thread(
            transcribe_audio, audio_path, settings.deepgram_api_key,
            settings.deepgram_model, project.language or settings.deepgram_language,
            settings.deepgram_diarize, settings.deepgram_smart_format,
        )
        markdown = format_transcript(result, settings.deepgram_diarize)
    except Exception as e:  # noqa: BLE001
        log.exception("transcribe failed")
        await status.edit_text(f"❌ Ошибка транскрибации: {e}")
        return

    # 2) Сохранение файлов
    try:
        saved = await asyncio.to_thread(
            save_transcript, settings, project, audio_path, markdown, result, name, topic
        )
    except Exception as e:  # noqa: BLE001
        log.exception("save failed")
        await status.edit_text(f"❌ Ошибка сохранения: {e}")
        return

    # 3) Git commit + push
    try:
        commit = await asyncio.to_thread(
            commit_and_push, settings.git_repo_dir, saved["rel_paths"],
            saved["commit_msg"], settings.git_branch,
            settings.git_author_name, settings.git_author_email,
        )
        await status.edit_text(
            f"✅ Готово: <code>{saved['rel_folder']}</code>\nCommit: <code>{commit}</code>"
        )
    except Exception as e:  # noqa: BLE001
        log.exception("git failed")
        await status.edit_text(f"⚠️ Сохранено локально, но git не прошёл: {e}")

    # 4) Уведомление в чат проекта / общий чат
    notify = project.notify_chat or settings.notify_chat_id
    if notify and int(notify) != tg_id:
        try:
            await bot.send_message(
                notify,
                f"📝 {project.name}: новый транскрипт «{topic}» от {name}"
            )
        except Exception as e:  # noqa: BLE001
            log.warning("notify failed: %s", e)


@router.message(CommandStart())
async def on_start(message: Message):
    settings = get_settings()
    if not _allowed(message.from_user.id):
        log.warning("denied start from %s", message.from_user.id)
        return
    projects = accessible_projects(settings, message.from_user.id)
    names = ", ".join(p.name for p in projects) or "—"
    await message.answer(
        "Привет! Я транскрибирую разговоры команды.\n\n"
        "Кинь голосовое или аудиофайл + комментарий, к какому проекту относится. "
        "Например: «Эрмитаж, звонок с подрядчиком по сметам».\n\n"
        f"Доступные проекты: {names}\n"
        "/projects — список · /help — справка"
    )


@router.message(Command("projects"))
async def on_projects(message: Message):
    settings = get_settings()
    if not _allowed(message.from_user.id):
        return
    projects = accessible_projects(settings, message.from_user.id)
    if not projects:
        await message.answer("Нет доступных проектов.")
        return
    lines = [f"• {p.name} — {p.folder}" for p in projects]
    await message.answer("Проекты:\n" + "\n".join(lines))


@router.message(Command("help"))
async def on_help(message: Message):
    if not _allowed(message.from_user.id):
        return
    await message.answer(
        "Отправь голосовое сообщение или аудиофайл.\n"
        "Если в подписи укажешь проект — сохраню сразу, иначе спрошу куда.\n\n"
        "Пример подписи: «Эрмитаж, созвон с подрядчиком»\n\n"
        "Транскрипты сохраняются в папке проекта в git-репозитории "
        "и помечаются в общем чате."
    )


@router.message(F.voice | F.audio)
async def on_audio(message: Message):
    settings = get_settings()
    tg_id = message.from_user.id
    if not _allowed(tg_id):
        log.warning("denied audio from %s", tg_id)
        return

    caption = message.caption or ""
    project = find_project(settings, tg_id, caption)

    # Скачиваем один раз, до возможного уточнения проекта
    file_id = message.voice.file_id if message.voice else message.audio.file_id
    dldir = Path("downloads") / str(tg_id)
    dldir.mkdir(parents=True, exist_ok=True)
    dest = dldir / f"{int(time.time())}{_ext_for(message)}"
    try:
        await _download(message.bot, file_id, dest)
    except Exception as e:  # noqa: BLE001
        log.exception("download failed")
        await message.answer(f"❌ Не смог скачать файл: {e}")
        return

    if project is not None:
        topic = extract_topic(caption, project)
        await _process(message.bot, tg_id, dest, project, topic)
        return

    # Проект не определён однозначно — уточняем
    text = "К какому проекту относится?" if not caption.strip() else "Не понял проект из подписи — выбери:"
    await message.answer(text, reply_markup=_projects_keyboard(settings, tg_id))
    _pending[tg_id] = {"audio_path": str(dest), "topic": caption}


@router.callback_query(F.data.startswith("proj:"))
async def on_project_choice(cb: CallbackQuery):
    await cb.answer()
    settings = get_settings()
    tg_id = cb.from_user.id
    if not _allowed(tg_id):
        return

    data = cb.data.split(":", 1)[1]
    pending = _pending.pop(tg_id, None)

    if data == "__cancel__":
        await cb.message.edit_text("Отменено.")
        return
    if pending is None:
        await cb.message.edit_text("Нечего обрабатывать — пришли аудио заново.")
        return

    if data == "__new__":
        _pending[tg_id] = {**pending, "awaiting_new_name": True}
        await cb.message.edit_text("Как назовём проект? Пришли название текстом.")
        return

    project = settings.project_by_id(data)
    if project is None:
        await cb.message.edit_text("Проект не найден.")
        return
    if project.member(tg_id) is None:
        await cb.message.edit_text("Нет доступа к этому проекту.")
        return

    topic = extract_topic(pending.get("topic", ""), project)
    await cb.message.edit_text(f"Обрабатываю «{topic}» → {project.name}…")
    await _process(cb.bot, tg_id, Path(pending["audio_path"]), project, topic)


@router.message(F.text)
async def on_text(message: Message):
    settings = get_settings()
    tg_id = message.from_user.id
    if not _allowed(tg_id):
        return

    pending = _pending.get(tg_id)
    if pending and pending.get("awaiting_new_name"):
        name = message.text.strip()
        _pending.pop(tg_id, None)
        project = create_project(settings, tg_id, name)
        if project is None:
            await message.answer("Не могу создать проект — нет прав.")
            return
        topic = extract_topic(pending.get("topic", ""), None)
        await message.answer(f"Создаю проект «{name}»…")
        await _process(message.bot, tg_id, Path(pending["audio_path"]), project, topic)
        return

    await message.answer("Пришли голосовое/аудиофайл. Проект можно указать в подписи.")


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    if not settings.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN не задан в .env")
    if not settings.deepgram_api_key:
        raise SystemExit("DEEPGRAM_API_KEY не задан в .env")

    bot = Bot(token=settings.telegram_token,
              default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    log.info("Team Transcribe запущен")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


def run():
    asyncio.run(main())
