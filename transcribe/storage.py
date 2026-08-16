"""Сохранение транскриптов: именование и запись .md в папку проекта.

Формат имени файла (без подчёркиваний, через « - »):
    2026-07-20 - телефон - Саша + Макс - обсуждение плана.md
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))

# Символы, недопустимые в именах файлов → заменяем на пробел
_FS_UNSAFE = re.compile(r'[\\/:*?"<>|\n\r\t]+')


def now_msk() -> datetime:
    return datetime.now(MSK)


def slugify(text: str, max_len: int = 60) -> str:
    """Слаг для технических id (латиница/кириллица, без пробелов)."""
    t = text.strip().lower()
    t = re.sub(r"[^a-zа-яё0-9]+", "-", t)
    t = re.sub(r"-+", "-", t).strip("-")
    return t[:max_len] or "bez-temy"


def sanitize_component(text: str, max_len: int = 80) -> str:
    """Чистит компонент имени файла: сохраняет пробелы/дефисы/кириллицу и
    регистр, убирает только символы, недопустимые в файловой системе."""
    t = re.sub(_FS_UNSAFE, " ", (text or "").strip())
    t = re.sub(r"\s+", " ", t)
    t = t.strip(" .-")
    return t[:max_len] or "bez-temy"


def build_filename(ts: datetime, channel: str, participants: str, topic: str) -> str:
    """Собирает имя: 2026 - 07 20 - телефон - Саша + Макс - обсуждение плана.md"""
    date = ts.strftime("%Y - %m %d")
    return (
        f"{date} - {sanitize_component(channel)} - "
        f"{sanitize_component(participants)} - {sanitize_component(topic)}.md"
    )


def save_transcript(settings, project, markdown: str, channel: str,
                    participants: str, topic: str) -> dict:
    """Пишет ТОЛЬКО .md в папку проекта (без JSON и без аудио)."""
    ts = now_msk()
    filename = build_filename(ts, channel, participants, topic)

    repo = Path(settings.git_repo_dir).expanduser()
    subfolder = settings.transcripts_subfolder
    folder = repo / project.folder / subfolder
    folder.mkdir(parents=True, exist_ok=True)

    md_path = folder / filename
    md_path.write_text(markdown, encoding="utf-8")

    rel_path = str(Path(project.folder) / subfolder / filename)
    return {
        "rel_paths": [rel_path],
        "commit_msg": f"transcribe: {topic} ({participants})",
        "rel_folder": f"{project.folder}{subfolder}/",
        "filename": filename,
    }
