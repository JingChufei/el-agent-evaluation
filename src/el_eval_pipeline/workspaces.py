from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from .common import ensure_dir, read_jsonl, sha256_file, write_json, write_jsonl
from .paths import as_posix_relative, resolve_repo_path


def _source_path_for_attachment(attachment: dict[str, Any], repo_root: Path) -> Path:
    source = resolve_repo_path(attachment.get("original_relative_path"), repo_root)
    if source is not None:
        return source
    return Path(attachment["original_path"])


def prepare_case_workspaces(
    cases_path: Path,
    output_dir: Path,
    *,
    repo_root: Path | None = None,
    copy_attachments: bool = True,
    portable_paths: bool = False,
) -> list[dict[str, Any]]:
    repo_root = repo_root or Path.cwd()
    cases = read_jsonl(cases_path)
    workspaces_root = ensure_dir(output_dir / "workspaces")
    run_manifest: list[dict[str, Any]] = []
    prepared_cases: list[dict[str, Any]] = []

    for case in cases:
        case_workspace = ensure_dir(workspaces_root / case["case_id"])
        inputs_dir = case_workspace / "inputs"
        if copy_attachments:
            ensure_dir(inputs_dir)
        elif inputs_dir.exists():
            shutil.rmtree(inputs_dir)
        sandbox_attachments: list[dict[str, Any]] = []
        for attachment in case.get("attachments", []):
            dest = inputs_dir / attachment["name"]
            sandbox_attachment = {
                **attachment,
                "sandbox_repo_relative_path": as_posix_relative(dest, repo_root),
                "sandbox_relative_path": dest.relative_to(case_workspace).as_posix(),
                "materialized": copy_attachments,
            }
            source = _source_path_for_attachment(attachment, repo_root)
            sandbox_attachment["original_relative_path"] = as_posix_relative(source, repo_root)
            if copy_attachments:
                shutil.copy2(source, dest)
                sandbox_attachment["sandbox_path"] = str(dest.resolve())
                sandbox_attachment["sandbox_sha256"] = sha256_file(dest)
            if portable_paths:
                sandbox_attachment.pop("original_path", None)
                sandbox_attachment.pop("sandbox_path", None)
            sandbox_attachments.append(sandbox_attachment)
        prompt_lines = [case["user_query"]]
        if sandbox_attachments:
            prompt_lines.append("")
            prompt_lines.append("附件:")
            for attachment in sandbox_attachments:
                prompt_lines.append(f"- {attachment['sandbox_relative_path']}")
        (case_workspace / "prompt.txt").write_text("\n".join(prompt_lines).strip() + "\n", encoding="utf-8")

        prepared_case = {
            **case,
            "workspace_repo_relative_path": as_posix_relative(case_workspace, repo_root),
            "attachments": sandbox_attachments,
        }
        if not portable_paths:
            prepared_case["workspace_path"] = str(case_workspace.resolve())
        write_json(case_workspace / "case_spec.json", prepared_case)
        prepared_cases.append(prepared_case)
        manifest_row = {
            "case_id": case["case_id"],
            "run_id": f"run-{case['case_id'].lower()}-{uuid.uuid4().hex[:8]}",
            "workspace_repo_relative_path": as_posix_relative(case_workspace, repo_root),
            "prompt_repo_relative_path": as_posix_relative(case_workspace / "prompt.txt", repo_root),
            "case_spec_repo_relative_path": as_posix_relative(case_workspace / "case_spec.json", repo_root),
            "attachment_count": len(sandbox_attachments),
            "attachments_materialized": copy_attachments,
            "status": "prepared_not_executed",
            "session_id": None,
            "turn_id": None,
        }
        if not portable_paths:
            manifest_row["workspace_path"] = str(case_workspace.resolve())
            manifest_row["prompt_path"] = str((case_workspace / "prompt.txt").resolve())
        run_manifest.append(manifest_row)

    write_jsonl(output_dir / "prepared_cases.jsonl", prepared_cases)
    write_json(output_dir / "run_manifest.json", run_manifest)
    return run_manifest
