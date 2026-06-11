from __future__ import annotations

from pathlib import Path


def repo_root_from(path: Path | None = None) -> Path:
    if path is not None:
        return path.resolve()
    return Path.cwd().resolve()


def as_posix_relative(path: Path, root: Path) -> str:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        return resolved_path.relative_to(resolved_root).as_posix()
    except ValueError:
        return resolved_path.as_posix()


def resolve_repo_path(value: str | None, root: Path) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return root / path
