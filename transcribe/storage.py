"""Сохранение транскриптов: именование файлов и запись в папку проекта."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

MSK = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    return datetime.now(MSK)


def slugify(text: str, max_len: int = 60) -> str:
    """Из «Звонок с подрядчиком по сметам» делает «zvonok-s-podryadchikom-po-smetam»."""
    t = text.strip().lower()
    t = re.sub(r"[^a-zа-яё0-9]+", "-", t)
    t = re.sub(r"-+", "-", t).strip("-")
    return t[:max_len] or "bez-temy"


def build_basename(ts: datetime, sender_name: str, topic: str) -> str:
    """YYYY-MM-DD_HHmm_<кто>_<тема> — единый стандарт имён файлов."""
    return f"{ts.strftime('%Y-%m-%d_%H%M')}_{slugify(sender_name)}_{slugify(topic)}"


def save_transcript(settings, project, audio_path: Path, markdown: str,
                    raw_result: dict, sender_name: str, topic: str) -> dict:
    """Пишет транскрипт (.md + .json + оригинал аудио) в папку проекта.

    Возвращает словарь с путями (относительно корня репо) и commit-сообщением.
    """
    ts = now_msk()
    base = build_basename(ts, sender_name, topic)

    repo = Path(settings.git_repo_dir).expanduser()
    subfolder = settings.transcripts_subfolder
    folder = repo / project.folder / subfolder
    folder.mkdir(parents=True, exist_ok=True)

    rel_base = Path(project.folder) / subfolder
    rel_paths: list[str] = []

    # 1) Markdown-транскрипт
    md_path = folder / f"{base}.md"
    md_path.write_text(markdown, encoding="utf-8")
    rel_paths.append(str(rel_base / md_path.name))

    # 2) Сырой JSON (для пере-обработки / аудита)
    json_path = folder / f"{base}.json"
    json_path.write_text(
        json.dumps(raw_result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rel_paths.append(str(rel_base / json_path.name))

    # 3) Оригинал аудио (опционально)
    if settings.save_original_audio:
        ext = audio_path.suffix.lower() or ".ogg"
        audio_dest = folder / f"{base}{ext}"
        shutil.copy2(audio_path, audio_dest)
        rel_paths.append(str(rel_base / audio_dest.name))

    return {
        "rel_paths": rel_paths,
        "commit_msg": f"transcribe: {topic} ({sender_name})",
        "rel_folder": f"{project.folder}{subfolder}/",
        "base": base,
    }
