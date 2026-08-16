"""Разбор метаданных разговора: канал, участники, тема.

Человек пишет всё одним сообщением, например:
    «телефон, Саша + Макс, обсуждение плана»
Бот разделяет на: канал, участников и тему.
"""
from __future__ import annotations

import re

# Канонические каналы и их синонимы (для распознавания из свободного текста).
CHANNEL_ALIASES = {
    "телефон": ["телефон", "по телефону", "звонок", "созвон", "phone", "call"],
    "телеграм": ["телеграм", "телега", "тг", "telegram"],
    "встреча": ["встреча", "офлайн", "очно", "meeting"],
    "зум": ["зум", "zoom", "видео", "видеозвонок", "вебинар", "meet"],
    "диктофон": ["диктофон", "надиктовал", "заметка", "диктовка", "voice"],
}


def canonical_channel(text: str) -> str:
    """Приводит текст к каноническому каналу; иначе возвращает как есть."""
    t = text.strip().lower()
    for canon, aliases in CHANNEL_ALIASES.items():
        for a in aliases:
            if len(a) <= 3:
                if t == a:
                    return canon
            elif a in t:
                return canon
    return text.strip()


def split_segments(text: str) -> list[str]:
    """Режет текст на сегменты по запятой/`;`/`|`, либо по « - »."""
    t = text.strip()
    parts = [p.strip() for p in re.split(r"[,;|]", t) if p.strip()]
    if len(parts) >= 2:
        return parts
    return [p.strip() for p in re.split(r"\s+-\s+", t) if p.strip()]


def _is_channel(text: str) -> bool:
    t = text.strip().lower()
    return any(
        a in t
        for aliases in CHANNEL_ALIASES.values()
        for a in aliases
        if len(a) > 3
    )


def parse_metadata(text: str) -> tuple[str | None, str | None, str | None]:
    """«телефон, Саша + Макс, обсуждение плана» → (канал, участники, тема).

    Поля, которые не удалось определить, возвращаются как None.
    """
    parts = split_segments(text)
    if not parts:
        return None, None, None

    if len(parts) >= 3:
        return canonical_channel(parts[0]), parts[1], " ".join(parts[2:]).strip()

    if len(parts) == 2:
        if _is_channel(parts[0]):
            return canonical_channel(parts[0]), parts[1], None
        return None, parts[0], parts[1]

    if _is_channel(parts[0]):
        return canonical_channel(parts[0]), None, None
    return None, None, parts[0]
