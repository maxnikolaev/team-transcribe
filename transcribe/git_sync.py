"""Синхронизация с git: pull → add → commit → push."""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("team-transcribe")

_GITHUB_RE = re.compile(
    r"(?:https?://|git@)github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def github_file_url(repo_url: str, branch: str, rel_path: str) -> str | None:
    """Строит прямую https-ссылку на файл в GitHub (blob). None, если не GitHub."""
    m = _GITHUB_RE.search((repo_url or "").strip())
    if not m:
        return None
    owner_repo = m.group(1).rstrip("/")
    quoted = quote(rel_path, safe="/")
    return f"https://github.com/{owner_repo}/blob/{branch}/{quoted}"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    log.info("git: %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True)


def ensure_repo(repo_url: str, repo_dir: str) -> Path:
    """Клонирует целевой репозиторий, если его ещё нет локально."""
    repo = Path(repo_dir).expanduser()
    if not (repo / ".git").exists():
        repo.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", repo_url, str(repo)], repo.parent)
    return repo


def commit_and_push(repo_dir: str, add_paths: list[str], commit_msg: str,
                    branch: str = "main", author_name: str = "",
                    author_email: str = "") -> str:
    """Добавляет файлы, коммитит и пушит. Возвращает короткий SHA коммита."""
    repo = Path(repo_dir).expanduser()
    if not (repo / ".git").exists():
        raise RuntimeError(f"Не git-репозиторий: {repo}")

    # 1) подтянуть чужие изменения (в репо могут пушить напрямую)
    _run(["git", "pull", "--rebase", "origin", branch], repo)

    # 2) добавить файлы
    for p in add_paths:
        _run(["git", "add", "--", p], repo)

    # 3) коммит (если пусто — git вернёт код 1, это нормально)
    cmd = ["git", "commit", "-m", commit_msg]
    if author_name and author_email:
        cmd += ["--author", f"{author_name} <{author_email}>"]
    _run(cmd, repo)

    # 4) push
    _run(["git", "push", "origin", branch], repo)

    head = _run(["git", "rev-parse", "--short", "HEAD"], repo)
    return head.stdout.strip()
