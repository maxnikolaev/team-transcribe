"""Telegram-бот: принимает аудио/видео/YouTube-ссылку, транскрибирует, сохраняет в git.

Поток: медиа → проект → канал → участники → тема → транскрибация → .md в git.
Человек может прислать метаданные одним сообщением («телефон, Саша + Макс,
обсуждение плана») — бот разделит, недостающее спросит.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
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
from .git_sync import commit_and_push, github_file_url
from .media import audio_duration, download_youtube_audio, extract_audio
from .metadata import CHANNEL_ALIASES, canonical_channel, parse_metadata, split_segments
from .project_router import (accessible_projects, can_create_projects,
                             create_project, extract_topic, find_project)
from .storage import save_transcript

log = logging.getLogger("team-transcribe")

router = Router()

# Состояние ожидания: tg_id -> {audio_path, project_id, channel, participants, topic}
_pending: dict[int, dict] = {}

LONG_TRANSCRIPT_SEC = 15 * 60   # длиннее 15 минут — показываем прогресс
PROGRESS_UPDATES = 3            # максимум промежуточных апдейтов (итого ≤ 4 сообщения)

_YOUTUBE_RE = re.compile(
    r"(?:https?://)?(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|youtu\.be/)[\w-]{6,}"
)


def extract_youtube_url(text: str) -> str | None:
    m = _YOUTUBE_RE.search(text or "")
    return m.group(0) if m else None


_YANDEX_DISK_RE = re.compile(
    r"(?:https?://)?disk\.yandex(?:\.ru|\.com)/(?:i|d)/[\w.-]+",
    re.IGNORECASE,
)


def extract_link(text: str) -> str | None:
    """Ссылка на видео: YouTube или Яндекс.Диск."""
    m = _YOUTUBE_RE.search(text or "") or _YANDEX_DISK_RE.search(text or "")
    return m.group(0) if m else None


def _shared_cookies() -> Path | None:
    """Общий серверный cookies-файл (если есть). None — если файла нет."""
    p = get_settings().cookies_path
    return p if p.exists() else None


_BOT_CHECK_RE = re.compile(
    r"(sign in to confirm|confirm you'?re not a bot|not a bot|requires login|"
    r"please log in|sign in|http error 429|cookies)", re.IGNORECASE,
)


def _needs_cookies(err: str) -> bool:
    """True, если ошибка похожа на «YouTube требует авторизацию / cookies протухли»."""
    return bool(_BOT_CHECK_RE.search(err or ""))


def _looks_like_cookies(path: Path) -> bool:
    """Минимальная проверка: Netscape cookies — строки с 7 колонками через таб."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return False
    for ln in lines[:50]:
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        if "\t" in ln and len(ln.split("\t")) >= 7:
            return True
    return False


def _allowed(tg_id: int) -> bool:
    return get_settings().user(tg_id) is not None


def _new_pending(audio_path: str) -> dict:
    return {"audio_path": audio_path, "cleanup": [audio_path], "project_id": None,
            "channel": None, "participants": None, "topic": None}


def _cleanup(paths: list[str]) -> None:
    """Удаляет временные медиафайлы, чтобы не захламлять сервер."""
    for p in paths or []:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass


def _fmt_dur(sec: float) -> str:
    """Форматирует секунды в читаемый вид: «2ч 03м», «5м 12с», «45с»."""
    sec = max(int(sec), 0)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}ч {m:02d}м"
    if m:
        return f"{m}м {s:02d}с"
    return f"{s}с"


def _estimate_seconds(duration: float) -> float:
    """Грубая оценка времени транскрибации (сек) по длительности аудио."""
    return max(duration / 6.0, 30.0)


def _next_missing(p: dict) -> str | None:
    if p["project_id"] is None:
        return "project"
    if not p["channel"]:
        return "channel"
    if not p["participants"]:
        return "participants"
    if not p["topic"]:
        return "topic"
    return None


def _fill(p: dict, channel, participants, topic) -> None:
    if channel and not p["channel"]:
        p["channel"] = channel
    if participants and not p["participants"]:
        p["participants"] = participants
    if topic and not p["topic"]:
        p["topic"] = topic


def _projects_keyboard(settings, tg_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=p.name, callback_data=f"proj:{p.id}")]
        for p in accessible_projects(settings, tg_id)
    ]
    if can_create_projects(settings, tg_id):
        rows.append([InlineKeyboardButton(text="➕ Новый проект", callback_data="proj:__new__")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="proj:__cancel__")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _channels_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=c, callback_data=f"chan:{c}")]
            for c in CHANNEL_ALIASES]
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


async def _advance(bot: Bot, tg_id: int) -> None:
    settings = get_settings()
    p = _pending.get(tg_id)
    if not p:
        return

    missing = _next_missing(p)
    if missing == "project":
        await bot.send_message(tg_id, "К какому проекту относится?",
                               reply_markup=_projects_keyboard(settings, tg_id))
    elif missing == "channel":
        await bot.send_message(tg_id, "В каком канале был разговор?",
                               reply_markup=_channels_keyboard())
    elif missing == "participants":
        await bot.send_message(tg_id, "Кто с кем разговаривал? (например: Саша + Макс)")
    elif missing == "topic":
        await bot.send_message(tg_id, "Какая тема? (например: обсуждение плана)")
    else:
        await _process(bot, tg_id, p)


async def _process(bot: Bot, tg_id: int, p: dict) -> None:
    settings = get_settings()
    project = settings.project_by_id(p["project_id"])
    if project is None:
        await bot.send_message(tg_id, "Проект не найден.")
        return

    channel, participants, topic = p["channel"], p["participants"], p["topic"]
    audio = Path(p["audio_path"])

    duration = await asyncio.to_thread(audio_duration, audio)
    long = duration is not None and duration > LONG_TRANSCRIPT_SEC
    est = _estimate_seconds(duration) if duration is not None else 0.0

    if long:
        status = await bot.send_message(
            tg_id,
            f"⏳ Транскрибирую… ({project.name})\n"
            f"Длительность: {_fmt_dur(duration or 0.0)}\n"
            f"Ориентировочно займёт ~{_fmt_dur(est)}.",
        )
    else:
        status = await bot.send_message(tg_id, f"⏳ Транскрибирую… ({project.name})")

    # 1) Транскрибация (с прогрессом для длинных файлов)
    task = asyncio.create_task(
        asyncio.to_thread(
            transcribe_audio, audio, settings.deepgram_api_key,
            settings.deepgram_model, project.language or settings.deepgram_language,
            settings.deepgram_diarize, settings.deepgram_smart_format,
        )
    )
    started = time.time()
    updates = 0
    try:
        if long:
            interval = max(est / (PROGRESS_UPDATES + 1), 20.0)
            while not task.done() and updates < PROGRESS_UPDATES:
                await asyncio.sleep(interval)
                if task.done():
                    break
                updates += 1
                elapsed = time.time() - started
                pct = min(int(elapsed / est * 100), 95)
                await status.edit_text(
                    f"⏳ Транскрибирую… ({project.name})\n"
                    f"Прошло {_fmt_dur(elapsed)} · ≈{pct}% · "
                    f"осталось ~{_fmt_dur(max(est - elapsed, 0))}"
                )
        result = await task
        markdown = format_transcript(result, settings.deepgram_diarize)
    except Exception as e:  # noqa: BLE001
        log.exception("transcribe failed")
        await status.edit_text(f"❌ Ошибка транскрибации: {e}")
        return
    finally:
        _cleanup(p.get("cleanup", []))  # временный медиафайл больше не нужен

    # 2) Сохранение (только .md)
    try:
        saved = await asyncio.to_thread(
            save_transcript, settings, project, markdown, channel, participants, topic
        )
    except Exception as e:  # noqa: BLE001
        log.exception("save failed")
        await status.edit_text(f"❌ Ошибка сохранения: {e}")
        return

    # 3) Git commit + push
    file_link = None
    try:
        commit = await asyncio.to_thread(
            commit_and_push, settings.git_repo_dir, saved["rel_paths"],
            saved["commit_msg"], settings.git_branch,
            settings.git_author_name, settings.git_author_email,
        )
        file_link = github_file_url(settings.git_repo_url, settings.git_branch,
                                    saved["rel_paths"][0])
        if file_link:
            await status.edit_text(
                f"✅ Готово: <a href=\"{file_link}\">{html.escape(saved['filename'])}</a>\n"
                f"Commit: <code>{commit}</code>\n{file_link}"
            )
        else:
            await status.edit_text(
                f"✅ Готово: <code>{saved['filename']}</code>\nCommit: <code>{commit}</code>"
            )
    except Exception as e:  # noqa: BLE001
        log.exception("git failed")
        await status.edit_text(f"⚠️ Сохранено локально, но git не прошёл: {e}")

    # 4) Уведомление в чат проекта / общий чат
    notify = project.notify_chat or settings.notify_chat_id
    if notify and int(notify) != tg_id:
        try:
            txt = f"📝 {project.name}: «{topic}» ({channel}, {participants})"
            if file_link:
                txt += f"\n{file_link}"
            await bot.send_message(notify, txt)
        except Exception as e:  # noqa: BLE001
            log.warning("notify failed: %s", e)

    _pending.pop(tg_id, None)


async def _start_processing(message: Message, audio_path: Path, meta_text: str) -> None:
    """Общая точка входа: получили аудио → парсим метаданные → запускаем диалог."""
    settings = get_settings()
    tg_id = message.from_user.id

    project = find_project(settings, tg_id, meta_text)
    project_id = project.id if project else None
    rest = extract_topic(meta_text, project) if project else meta_text
    channel, participants, topic = parse_metadata(rest)

    pending = _new_pending(str(audio_path))
    pending["project_id"] = project_id
    _fill(pending, channel, participants, topic)
    _pending[tg_id] = pending
    await _advance(message.bot, tg_id)


async def _handle_link(message: Message, url: str, meta_text: str,
                       cookies: Path | None) -> None:
    settings = get_settings()
    tg_id = message.from_user.id
    status = await message.answer("⏬ Скачиваю аудио…")
    try:
        audio = await asyncio.to_thread(
            download_youtube_audio, url, Path("downloads") / str(tg_id), cookies
        )
    except Exception as e:  # noqa: BLE001
        log.exception("download failed")
        if _needs_cookies(str(e)):
            await status.edit_text(
                "❌ YouTube не отдаёт аудио без авторизации — cookies протухли "
                "или их нет.\n\nПришли новый файл cookies.txt (Netscape-формат, "
                "экспорт «Get cookies.txt LOCALLY»). Первый рабочий файл — "
                "побеждает, и я использую его до следующего сбоя."
            )
        else:
            await status.edit_text(f"❌ Не удалось скачать: {e}")
        return

    await status.edit_text("✅ Аудио получено.")
    rest = meta_text.replace(url, "", 1).strip()
    await _start_processing(message, audio, rest)


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
        "Принимаю:\n"
        "• голосовое / аудиофайл\n"
        "• видеофайл (MP4) — извлеку звук\n"
        "• ссылку на YouTube — скачаю аудио\n"
        "• ссылку на Яндекс.Диск (видео) — скачаю аудио\n\n"
        "Можно сразу всё одним сообщением:\n"
        "«Эрмитаж, телефон, Саша + Макс, обсуждение плана»\n"
        "Чего не хватит — спрошу отдельно.\n\n"
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
        "Что можно прислать:\n"
        "• голосовое или аудиофайл\n"
        "• видеофайл (MP4) — бот извлечёт звук\n"
        "• ссылку на YouTube (текстом) — бот скачает аудио\n"
        "• ссылку на Яндекс.Диск (текстом) — бот скачает аудио\n\n"
        "Для YouTube с защитой: пришли cookies.txt файлом\n"
        "(файл .txt, затем ссылку — либо файл с подписью-ссылкой).\n\n"
        "В подписи можно указать всё сразу:\n"
        "«проект, канал, участники, тема»\n"
        "Пример: «Эрмитаж, телефон, Саша + Макс, обсуждение плана»\n\n"
        "Транскрипт сохранится как:\n"
        "2026 - 07 20 - телефон - Саша + Макс - обсуждение плана.md"
    )


@router.message(F.voice | F.audio)
async def on_audio(message: Message):
    settings = get_settings()
    tg_id = message.from_user.id
    if not _allowed(tg_id):
        log.warning("denied audio from %s", tg_id)
        return

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

    await _start_processing(message, dest, message.caption or "")


@router.message(F.video)
async def on_video(message: Message):
    settings = get_settings()
    tg_id = message.from_user.id
    if not _allowed(tg_id):
        log.warning("denied video from %s", tg_id)
        return

    dldir = Path("downloads") / str(tg_id)
    dldir.mkdir(parents=True, exist_ok=True)
    dest = dldir / f"{int(time.time())}.mp4"
    try:
        await _download(message.bot, message.video.file_id, dest)
        audio = dldir / f"{int(time.time())}.wav"
        await asyncio.to_thread(extract_audio, dest, audio)
        dest.unlink(missing_ok=True)  # оригинал видео после извлечения не нужен
    except Exception as e:  # noqa: BLE001
        log.exception("video extract failed")
        await message.answer(f"❌ Не удалось извлечь аудио из видео: {e}")
        return

    await _start_processing(message, audio, message.caption or "")


@router.message(F.document)
async def on_document(message: Message):
    settings = get_settings()
    tg_id = message.from_user.id
    if not _allowed(tg_id):
        return

    doc = message.document
    name = (doc.file_name or "").lower()
    # Принимаем только cookies-файлы (.txt или имя содержит cookie)
    if not (name.endswith(".txt") or "cookie" in name):
        await message.answer("Пришли cookies-файл в формате .txt (Netscape cookies).")
        return

    cookies_path = settings.cookies_path
    cookies_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await _download(message.bot, doc.file_id, cookies_path)
        cookies_path.chmod(0o600)
    except Exception as e:  # noqa: BLE001
        log.exception("cookies download failed")
        await message.answer(f"❌ Не смог скачать cookies: {e}")
        return

    if not _looks_like_cookies(cookies_path):
        await message.answer(
            "❌ Это не похоже на cookies.txt (Netscape). Нужен экспорт расширением "
            "«Get cookies.txt LOCALLY»."
        )
        return

    caption = message.caption or ""
    url = extract_youtube_url(caption)
    if url:
        await message.answer("Cookies обновлены ✓. Пробую скачать YouTube…")
        await _handle_link(message, url, caption, cookies_path)
    else:
        await message.answer("Cookies обновлены ✓. Теперь кинь ссылку на YouTube.")


@router.callback_query(F.data.startswith("proj:"))
async def on_project_choice(cb: CallbackQuery):
    await cb.answer()
    settings = get_settings()
    tg_id = cb.from_user.id
    if not _allowed(tg_id):
        return

    data = cb.data.split(":", 1)[1]
    pending = _pending.get(tg_id)

    if data == "__cancel__":
        p = _pending.pop(tg_id, None)
        if p:
            _cleanup(p.get("cleanup", []))
        await cb.message.edit_text("Отменено.")
        return
    if pending is None:
        await cb.message.edit_text("Нечего обрабатывать — пришли аудио заново.")
        return

    if data == "__new__":
        pending["_await_new_name"] = True
        await cb.message.edit_text("Как назовём проект? Пришли название текстом.")
        return

    project = settings.project_by_id(data)
    if project is None:
        await cb.message.edit_text("Проект не найден.")
        return
    if project.member(tg_id) is None:
        await cb.message.edit_text("Нет доступа к этому проекту.")
        return

    pending["project_id"] = data
    await cb.message.edit_text(f"Проект: {project.name}")
    await _advance(cb.bot, tg_id)


@router.callback_query(F.data.startswith("chan:"))
async def on_channel_choice(cb: CallbackQuery):
    await cb.answer()
    tg_id = cb.from_user.id
    pending = _pending.get(tg_id)
    if not pending:
        await cb.message.edit_text("Нечего обрабатывать.")
        return

    channel = cb.data.split(":", 1)[1]
    pending["channel"] = channel
    await cb.message.edit_text(f"Канал: {channel}")
    await _advance(cb.bot, tg_id)


@router.message(F.text)
async def on_text(message: Message):
    settings = get_settings()
    tg_id = message.from_user.id
    if not _allowed(tg_id):
        return

    text = message.text.strip()

    # Ссылка на видео (YouTube / Яндекс.Диск) → новая задача
    url = extract_link(text)
    if url:
        cookies = _shared_cookies() if _YOUTUBE_RE.search(url) else None
        await _handle_link(message, url, text, cookies)
        return

    pending = _pending.get(tg_id)
    if not pending:
        await message.answer("Пришли голосовое/аудио/видео или ссылку на YouTube.")
        return

    # Ожидание имени нового проекта
    if pending.get("_await_new_name"):
        pending.pop("_await_new_name", None)
        _pending.pop(tg_id, None)
        project = create_project(settings, tg_id, text)
        if project is None:
            await message.answer("Не могу создать проект — нет прав.")
            return
        pending["project_id"] = project.id
        _pending[tg_id] = pending
        await message.answer(f"Создан проект «{project.name}».")
        await _advance(message.bot, tg_id)
        return

    missing = _next_missing(pending)

    if missing == "project":
        proj = find_project(settings, tg_id, text)
        if proj:
            pending["project_id"] = proj.id
            rest = extract_topic(text, proj)
            if len(split_segments(rest)) >= 2:
                ch, part, top = parse_metadata(rest)
                _fill(pending, ch, part, top)
        # если проект не распознан — _advance переспросит

    elif missing in ("channel", "participants", "topic"):
        segs = split_segments(text)
        if len(segs) >= 2:
            ch, part, top = parse_metadata(text)
            _fill(pending, ch, part, top)
        else:
            if missing == "channel":
                pending["channel"] = canonical_channel(text) or text
            elif missing == "participants":
                pending["participants"] = text
            else:
                pending["topic"] = text

    await _advance(message.bot, tg_id)


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
