"""Получение аудио из разных источников: видеофайл (ffmpeg), YouTube (yt-dlp)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def extract_audio(src: Path, dst: Path) -> Path:
    """Извлекает аудиодорожку из видеофайла в 16kHz mono WAV (ffmpeg)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
         "-vn", "-ar", "16000", "-ac", "1", "-f", "wav", str(dst)],
        check=True,
    )
    return dst


def download_youtube_audio(url: str, out_dir: Path,
                           cookies_path: Path | None = None) -> Path:
    """Скачивает аудио с YouTube через yt-dlp (mp3). Возвращает путь к файлу.

    cookies_path — путь к cookies.txt (Netscape-формат), опционально.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    base = [
        sys.executable, "-m", "yt_dlp",
        "--js-runtimes", "node",          # решает «n challenge» YouTube (нужен node + yt-dlp-ejs)
        "-x", "--audio-format", "mp3", "--audio-quality", "0",
        "--no-playlist", "--no-warnings",
        "-o", str(out_dir / "%(id)s.%(ext)s"),
        "--print", "after_move:filepath",
    ]
    if cookies_path and Path(cookies_path).exists():
        base += ["--cookies", str(cookies_path)]

    # Пробуем аудио-формат; при «format not available» — формат по умолчанию.
    last_err = "yt-dlp error"
    for fmt in (["bestaudio/best"], []):
        cmd = base + (["-f"] + fmt if fmt else []) + [url]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if r.returncode == 0:
            break
        last_err = (r.stderr or "yt-dlp error").strip()[-500:]
    else:
        raise RuntimeError(last_err)

    # yt-dlp печатает путь к итоговому файлу последней строкой stdout
    for line in reversed(r.stdout.strip().splitlines()):
        line = line.strip()
        if line.lower().endswith(".mp3") and Path(line).exists():
            return Path(line)

    # fallback: самый свежий mp3 в каталоге
    mp3s = sorted(out_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if mp3s:
        return mp3s[0]

    raise RuntimeError("yt-dlp не вернул путь к аудиофайлу")
