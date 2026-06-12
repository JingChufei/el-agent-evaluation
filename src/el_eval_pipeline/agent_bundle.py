from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .common import ensure_dir, read_jsonl, sha256_file, write_json
from .paths import as_posix_relative, resolve_repo_path


FORBIDDEN_AGENT_KEYS = {
    "annotation_status",
    "answer_image",
    "d2_enabled",
    "d3_candidate",
    "d3_priority",
    "evaluable_dimensions",
    "expected_answer",
    "gold_chain",
    "raw_annotations",
    "reference_artifacts",
    "reference_answer",
    "supplemental_annotation",
    "target_state",
}

AGENT_CASE_KEYS = {
    "case_id",
    "task_type",
    "user_query",
}

AGENT_ATTACHMENT_KEYS = {
    "extension",
    "mime_type",
    "name",
    "relative_path",
    "sandbox_relative_path",
    "sha256",
    "size_bytes",
    "source_token",
}


def _load_json(path: Path) -> Any:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _copy_file(source: Path, dest: Path) -> None:
    ensure_dir(dest.parent)
    shutil.copy2(source, dest)


def _attachment_source(attachment: dict[str, Any], repo_root: Path) -> Path:
    source = resolve_repo_path(attachment.get("original_relative_path"), repo_root)
    if source is not None:
        return source
    return Path(attachment["original_path"])


def _agent_attachment_name(attachment: dict[str, Any]) -> str:
    digest = str(attachment.get("sha256") or "")[:12]
    name = str(attachment.get("name") or "attachment")
    return f"{digest}_{name}" if digest else name


def sanitize_case_for_agent(
    case: dict[str, Any],
    *,
    attachment_paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    sanitized = {key: case[key] for key in AGENT_CASE_KEYS if key in case}
    sanitized_attachments: list[dict[str, Any]] = []
    for attachment in case.get("attachments", []):
        if not isinstance(attachment, dict):
            continue
        item = {key: attachment[key] for key in AGENT_ATTACHMENT_KEYS if key in attachment}
        attachment_name = str(attachment.get("name") or "")
        if attachment_paths and attachment_name in attachment_paths:
            item["original_relative_path"] = attachment_paths[attachment_name]
        elif attachment.get("original_relative_path"):
            item["original_relative_path"] = attachment["original_relative_path"]
        item["sandbox_relative_path"] = item.get("sandbox_relative_path") or f"inputs/{attachment_name}"
        item["materialized"] = False
        sanitized_attachments.append(item)
    sanitized["attachments"] = sanitized_attachments
    return sanitized


def _find_forbidden_keys(value: Any, *, path: str = "$") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            if key in FORBIDDEN_AGENT_KEYS:
                hits.append(nested_path)
            hits.extend(_find_forbidden_keys(nested, path=nested_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            hits.extend(_find_forbidden_keys(nested, path=f"{path}[{index}]"))
    return hits


def scan_agent_bundle_for_forbidden_keys(bundle_dir: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in sorted(bundle_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".jsonl"}:
            continue
        if path.name == "leak_scan_report.json":
            continue
        if path.suffix == ".jsonl":
            for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                if not line.strip():
                    continue
                hits = _find_forbidden_keys(_load_jsonl_line(line))
                if hits:
                    findings.append({"path": path.relative_to(bundle_dir).as_posix(), "line": line_number, "forbidden_keys": hits})
        else:
            hits = _find_forbidden_keys(_load_json(path))
            if hits:
                findings.append({"path": path.relative_to(bundle_dir).as_posix(), "forbidden_keys": hits})
    return findings


def _load_jsonl_line(line: str) -> Any:
    import json

    return json.loads(line)


def assert_agent_spec_is_sanitized(case_spec: dict[str, Any]) -> None:
    hits = _find_forbidden_keys(case_spec)
    if hits:
        raise ValueError(f"agent-visible case spec contains forbidden GT keys: {', '.join(hits[:20])}")


def _copy_runner_code(repo_root: Path, bundle_dir: Path) -> None:
    _copy_file(repo_root / "pyproject.toml", bundle_dir / "pyproject.toml")
    if (repo_root / "scripts" / "openclaw_runner.py").exists():
        _copy_file(repo_root / "scripts" / "openclaw_runner.py", bundle_dir / "scripts" / "openclaw_runner.py")
    src_root = repo_root / "src"
    dest_src = bundle_dir / "src"
    if dest_src.exists():
        shutil.rmtree(dest_src)
    shutil.copytree(src_root, dest_src, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))


def prepare_agent_execution_bundle(
    *,
    source_bundle_dir: Path,
    output_dir: Path,
    repo_root: Path | None = None,
    include_code: bool = True,
) -> dict[str, Any]:
    repo_root = repo_root or Path.cwd()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    ensure_dir(output_dir)

    source_manifest = _load_json(source_bundle_dir / "run_manifest.json")
    if not isinstance(source_manifest, list):
        raise ValueError(f"run manifest must be a JSON array: {source_bundle_dir / 'run_manifest.json'}")

    output_manifest: list[dict[str, Any]] = []
    copied_attachments: dict[str, str] = {}
    case_count = 0

    for manifest_row in source_manifest:
        if not isinstance(manifest_row, dict):
            continue
        case_id = str(manifest_row["case_id"])
        source_workspace = resolve_repo_path(manifest_row.get("workspace_repo_relative_path"), repo_root) or Path(manifest_row["workspace_path"])
        source_prompt = resolve_repo_path(manifest_row.get("prompt_repo_relative_path"), repo_root) or source_workspace / "prompt.txt"
        source_case_spec = resolve_repo_path(manifest_row.get("case_spec_repo_relative_path"), repo_root) or source_workspace / "case_spec.json"
        case = _load_json(source_case_spec)

        output_workspace = ensure_dir(output_dir / "workspaces" / case_id)
        _copy_file(source_prompt, output_workspace / "prompt.txt")

        attachment_paths: dict[str, str] = {}
        for attachment in case.get("attachments", []):
            source = _attachment_source(attachment, repo_root)
            dest_name = _agent_attachment_name(attachment)
            dest = output_dir / "attachments" / dest_name
            if dest_name not in copied_attachments:
                _copy_file(source, dest)
                copied_attachments[dest_name] = sha256_file(dest)
            attachment_paths[str(attachment.get("name") or "")] = as_posix_relative(dest, output_dir)

        agent_case = sanitize_case_for_agent(case, attachment_paths=attachment_paths)
        assert_agent_spec_is_sanitized(agent_case)
        write_json(output_workspace / "agent_case_spec.json", agent_case)

        output_manifest.append(
            {
                "case_id": case_id,
                "run_id": manifest_row.get("run_id"),
                "workspace_repo_relative_path": as_posix_relative(output_workspace, output_dir),
                "prompt_repo_relative_path": as_posix_relative(output_workspace / "prompt.txt", output_dir),
                "case_spec_repo_relative_path": as_posix_relative(output_workspace / "agent_case_spec.json", output_dir),
                "attachment_count": len(agent_case.get("attachments", [])),
                "attachments_materialized": False,
                "status": "prepared_not_executed",
                "session_id": None,
                "turn_id": None,
            }
        )
        case_count += 1

    write_json(output_dir / "run_manifest.json", output_manifest)
    if include_code:
        _copy_runner_code(repo_root, output_dir)

    findings = scan_agent_bundle_for_forbidden_keys(output_dir)
    report = {
        "status": "passed" if not findings else "failed",
        "case_count": case_count,
        "attachment_count": len(copied_attachments),
        "forbidden_keys": sorted(FORBIDDEN_AGENT_KEYS),
        "findings": findings,
        "include_code": include_code,
    }
    write_json(output_dir / "leak_scan_report.json", report)
    if findings:
        raise ValueError(f"agent execution bundle contains forbidden GT keys: {findings[:5]}")
    return report
