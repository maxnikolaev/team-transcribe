"""Загрузка конфигурации: .env (секреты) + projects.yaml (проекты/доступы)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class User:
    tg_id: int
    name: str
    role: str = "member"


@dataclass
class Project:
    id: str
    name: str
    folder: str
    language: str = ""
    members: list[dict] = field(default_factory=list)
    notify_chat: int | None = None
    aliases: list[str] = field(default_factory=list)

    def member(self, tg_id: int) -> dict | None:
        for m in self.members:
            if int(m.get("tg_id", -1)) == tg_id:
                return m
        return None


@dataclass
class Settings:
    telegram_token: str
    deepgram_api_key: str
    deepgram_model: str = "nova-3"
    deepgram_language: str = "ru"
    deepgram_diarize: bool = True
    deepgram_smart_format: bool = True
    git_repo_url: str = ""
    git_repo_dir: str = ""
    git_branch: str = "main"
    git_author_name: str = ""
    git_author_email: str = ""
    transcripts_subfolder: str = "звонки"
    save_original_audio: bool = True
    notify_chat_id: int | None = None
    allowed_users: list[User] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    config_path: Path = BASE_DIR / "config" / "projects.yaml"

    def user(self, tg_id: int) -> User | None:
        for u in self.allowed_users:
            if u.tg_id == tg_id:
                return u
        return None

    def project_by_id(self, pid: str) -> Project | None:
        for p in self.projects:
            if p.id == pid:
                return p
        return None


def load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env", override=False)

    config_path = Path(os.getenv("CONFIG_PATH", "") or (BASE_DIR / "config" / "projects.yaml"))
    raw: dict[str, Any] = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    users = [
        User(tg_id=int(u["tg_id"]), name=u.get("name", ""), role=u.get("role", "member"))
        for u in raw.get("allowed_users", [])
    ]

    projects = []
    for p in raw.get("projects", []):
        projects.append(
            Project(
                id=str(p["id"]),
                name=p.get("name", p["id"]),
                folder=p.get("folder", ""),
                language=p.get("language", "") or _env("DEEPGRAM_LANGUAGE", "ru"),
                members=p.get("members", []),
                notify_chat=p.get("notify_chat"),
                aliases=[str(a) for a in p.get("aliases", [])],
            )
        )

    nc = _env("NOTIFY_CHAT_ID")
    return Settings(
        telegram_token=_env("TELEGRAM_BOT_TOKEN"),
        deepgram_api_key=_env("DEEPGRAM_API_KEY"),
        deepgram_model=_env("DEEPGRAM_MODEL", "nova-3"),
        deepgram_language=_env("DEEPGRAM_LANGUAGE", "ru"),
        deepgram_diarize=_env_bool("DEEPGRAM_DIARIZE", True),
        deepgram_smart_format=_env_bool("DEEPGRAM_SMART_FORMAT", True),
        git_repo_url=_env("GIT_REPO_URL"),
        git_repo_dir=_env("GIT_REPO_DIR"),
        git_branch=_env("GIT_BRANCH", "main"),
        git_author_name=_env("GIT_AUTHOR_NAME"),
        git_author_email=_env("GIT_AUTHOR_EMAIL"),
        transcripts_subfolder=_env("TRANSCRIPTS_SUBFOLDER", "транскрибации"),
        save_original_audio=_env_bool("SAVE_ORIGINAL_AUDIO", True),
        notify_chat_id=int(nc) if nc else None,
        allowed_users=users,
        projects=projects,
        config_path=config_path,
    )


def persist_project(settings: Settings, project: Project) -> None:
    """Дописывает новый проект в config/projects.yaml (чтобы пережил рестарт)."""
    raw: dict[str, Any] = {}
    if settings.config_path.exists():
        raw = yaml.safe_load(settings.config_path.read_text(encoding="utf-8")) or {}

    raw.setdefault("projects", []).append(
        {
            "id": project.id,
            "name": project.name,
            "folder": project.folder,
            "language": project.language,
            "members": project.members,
            "aliases": project.aliases,
        }
    )
    settings.config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings
