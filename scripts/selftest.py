"""Самопроверка: парсинг метаданных, имя файла, сохранение только .md."""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcribe.metadata import parse_metadata
from transcribe.storage import build_filename, save_transcript
from transcribe.config import get_settings, Project

print("=== parse_metadata ===")
for t in [
    "телефон, Саша + Макс, обсуждение плана",
    "телеграм - Иван и Пётр - созвон",
    "зум, команда, недельный план",
    "обсуждение плана",
    "телефон",
]:
    print(f"  {t!r:50} -> {parse_metadata(t)}")

print("=== build_filename ===")
ts = datetime(2026, 7, 20, tzinfo=timezone(timedelta(hours=3)))
print("  ", build_filename(ts, "телефон", "Саша + Макс", "обсуждение плана"))

print("=== save_transcript (only .md) ===")
tmp = tempfile.mkdtemp()
s = get_settings()
s.git_repo_dir = tmp
p = Project(id="t", name="Тест", folder="cases/test/", language="ru")
r = save_transcript(s, p, "# Транскрипт\nПривет.", "телефон", "Саша + Макс", "обсуждение плана")
print("  filename:", r["filename"])
print("  rel_paths:", r["rel_paths"])
written = []
for root, _, files in os.walk(tmp):
    for f in files:
        written.append(os.path.relpath(os.path.join(root, f), tmp))
print("  written files:", written)
assert len(written) == 1 and written[0].endswith(".md"), "Должен быть только один .md!"
shutil.rmtree(tmp)
print("OK")
