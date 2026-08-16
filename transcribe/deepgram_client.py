"""Клиент Deepgram: транскрибация аудиофайла через pre-recorded REST API.

Используется pre-recorded endpoint (не стриминг): мы отправляем готовый
файл целиком и получаем JSON с текстом и (при diarize=true) разбивкой
по говорящим. Для файлов-записей звонков/диктофона это правильный режим.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import httpx

log = logging.getLogger("team-transcribe")

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"

CONTENT_TYPES = {
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}


def _content_type(path: Path) -> str:
    return CONTENT_TYPES.get(path.suffix.lower(), "audio/wav")


def _convert_to_wav(src: Path, dst: Path) -> Path:
    """Конвертация любого аудио в 16kHz mono WAV (fallback через ffmpeg)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-ar", "16000", "-ac", "1", "-f", "wav", str(dst)],
        check=True,
    )
    return dst


def transcribe(path: Path, api_key: str, model: str = "nova-3",
               language: str = "ru", diarize: bool = True,
               smart_format: bool = True) -> dict:
    """Транскрибирует файл и возвращает сырой JSON-ответ Deepgram (dict).

    Сначала пробует отправить оригинал как есть; если формат неизвестен
    или запрос падает — конвертирует в WAV и пробует ещё раз.
    """
    params = {
        "model": model,
        "smart_format": "true" if smart_format else "false",
        "diarize": "true" if diarize else "false",
        "punctuate": "true",
    }
    if language:
        params["language"] = language

    candidates = [(path, _content_type(path))]
    if path.suffix.lower() not in CONTENT_TYPES:
        candidates.append((_convert_to_wav(path, path.with_suffix(".wav")), "audio/wav"))

    last_error: Exception | None = None
    for cpath, ctype in candidates:
        try:
            data = cpath.read_bytes()
            with httpx.Client(timeout=600) as client:
                resp = client.post(
                    DEEPGRAM_URL,
                    params=params,
                    headers={"Authorization": f"Token {api_key}", "Content-Type": ctype},
                    content=data,
                )
            if resp.status_code == 200:
                return resp.json()
            last_error = RuntimeError(
                f"Deepgram HTTP {resp.status_code}: {resp.text[:400]}"
            )
        except Exception as e:  # noqa: BLE001
            last_error = e

    raise RuntimeError(f"Транскрибация не удалась: {last_error}")


def _fmt_ts(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _get_paragraph_list(alt: dict) -> list[dict]:
    """Достаёт список абзацев. В pre-recorded ответе `paragraphs` — это
    dict вида {transcript, paragraphs: [...]}, поэтому нормализуем."""
    paras = alt.get("paragraphs")
    if isinstance(paras, dict):
        inner = paras.get("paragraphs")
        return inner if isinstance(inner, list) else []
    if isinstance(paras, list):
        return paras
    return []


def format_transcript(result: dict, diarize: bool) -> str:
    """Превращает ответ Deepgram в Markdown-текст транскрипта."""
    try:
        alt = result["results"]["channels"][0]["alternatives"][0]
    except (KeyError, IndexError):
        return "_(пустой результат)_"

    transcript = alt.get("transcript", "").strip()
    if not transcript:
        return "_(пустой результат)_"

    paragraphs = _get_paragraph_list(alt)

    if not diarize:
        parts = []
        for p in paragraphs:
            sents = p.get("sentences") or []
            text = " ".join(
                s.get("text", "").strip() for s in sents if s.get("text", "").strip()
            )
            if not text:
                text = (p.get("transcript") or "").strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts) if parts else transcript

    # Диаризация: по абзацам со спикерами и таймкодами.
    blocks = []
    for p in paragraphs:
        speaker = int(p.get("speaker", 0))
        sents = p.get("sentences") or []
        lines = []
        for s in sents:
            t = s.get("text", "").strip()
            if t:
                lines.append(f"- [{_fmt_ts(s.get('start', 0))}] {t}")
        text = "\n".join(lines) if lines else (p.get("transcript") or "").strip()
        if text:
            blocks.append(f"### Спикер {speaker + 1}\n{text}")

    return "\n\n".join(blocks) if blocks else transcript
