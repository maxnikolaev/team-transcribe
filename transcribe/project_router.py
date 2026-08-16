"""Маршрутизация: определить проект по тексту + проверить доступ."""
from __future__ import annotations

import re

from .config import Project, Settings, get_settings
from .storage import now_msk, slugify


def accessible_projects(settings: Settings, tg_id: int) -> list[Project]:
    return [p for p in settings.projects if p.member(tg_id) is not None]


def can_create_projects(settings: Settings, tg_id: int) -> bool:
    u = settings.user(tg_id)
    if u and u.role == "owner":
        return True
    for p in settings.projects:
        m = p.member(tg_id)
        if m and m.get("can_create_folders"):
            return True
    return False


def find_project(settings: Settings, tg_id: int, text: str) -> Project | None:
    """Ищет проект по упоминанию в тексте среди доступных пользователю.

    Матчится по id, полному имени, алиасам, а также по отдельным словам
    имени (>=3 символа) — чтобы «Эрмитаж» находил «Усадьбу Эрмитаж».
    Возвращает проект только при единственном совпадении, иначе None.
    """
    t = text.lower()
    matches = []
    for p in accessible_projects(settings, tg_id):
        if p.id.lower() in t or p.name.lower() in t:
            matches.append(p)
            continue
        if any(a.lower() in t for a in p.aliases):
            matches.append(p)
            continue
        words = [w for w in re.findall(r"[a-zа-яё0-9]+", p.name.lower()) if len(w) >= 3]
        if any(w in t for w in words):
            matches.append(p)
    return matches[0] if len(matches) == 1 else None


def extract_topic(text: str, project: Project | None) -> str:
    """Вычищает упоминание проекта из текста, оставляя «тему»."""
    if not project:
        return text.strip() or "bez-temy"
    t = text
    tokens = [project.name, project.id] + list(project.aliases)
    tokens += [w for w in re.findall(r"[a-zа-яё0-9]+", project.name.lower()) if len(w) >= 3]
    for tok in tokens:
        t = re.sub(re.escape(tok), "", t, flags=re.IGNORECASE)
    t = re.sub(r"^[\s,;:—\-–]+", "", t).strip()
    return t or "bez-temy"


def create_project(settings: Settings, tg_id: int, name: str) -> Project | None:
    """Создаёт новый проект (если у пользователя есть права) и персистит его."""
    if not can_create_projects(settings, tg_id):
        return None

    pid = slugify(name) or "project"
    folder = f"cases/{now_msk().strftime('%Y-%m-%d')} - {name}/"
    project = Project(
        id=pid,
        name=name,
        folder=folder,
        language=settings.deepgram_language,
        members=[{"tg_id": tg_id, "role": "owner", "can_create_folders": True}],
    )
    settings.projects.append(project)

    from .config import persist_project
    persist_project(settings, project)
    return project


def sender_name(settings: Settings, tg_id: int) -> str:
    u = settings.user(tg_id)
    return u.name if u else str(tg_id)
