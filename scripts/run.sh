#!/usr/bin/env bash
# Запуск бота. Предполагает, что venv создан: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m transcribe
