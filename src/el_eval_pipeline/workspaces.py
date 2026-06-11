from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from .common import ensure_dir, read_jsonl, sha256_file, write_json, write_jsonl
from .paths import as_posix_relative, resolve_repo_path


def prepare_case_workspaces(cases_path: Path, output_dir: Path, *, repo_root: Path | None = None) -> list[dict[str, Any]]:
    repo_root = repo_root or Path.cwd()
    cases = read_jsonl(cases_path)
    workspaces_root = ensure_dir(output_dir / "workspaces")
    run_manifest: list[dict[str, Any]] = []
    prepared_cases: list[dict[str, Any]] = []

    for case in cases:
        case_workspace = ensure_dir(workspaces_root / case["case_id"])
        inputs_dir = ensure_dir(case_workspace / "inputs")
        sandbox_attachments: list[dict[str, Any]] = []
        for attachment in case.get("attachments", []):
            source = resolve_repo_path(attachment.get("original_relative_path"), repo_root) or Path(attachment["original_path"])
            dest = inputs_dir / attachment["name"]
            shutil.copy2(source, dest)
            sandbox_attachments.append(
                {
                    **attachment,
                    "original_relative_path": as_posix_relative(source, repo_root),
                    "sandbox_path": str(dest.resolve()),
                    "sandbox_repo_relative_path": as_posix_relative(dest, repo_root),
                    "sandbox_relative_path": str(dest.relative_to(case_workspace)),
                    "sandbox_sha256": sha256_file(dest),
                }
            )
        prompt_lines = [case["user_query"]]
        if sandbox_attachments:
            prompt_lines.append("")
            prompt_lines.append("附件:")
            for attachment in sandbox_attachments:
                prompt_lines.append(f"- {attachment['sandbox_relative_path']}")
        (case_workspace / "prompt.txt").write_text("\n".join(prompt_lines).strip() + "\n", encoding="utf-8")

        prepared_case = {
            **case,
            "workspace_path": str(case_workspace.resolve()),
            "workspace_repo_relative_path": as_posix_relative(case_workspace, repo_root),
            "attachments": sandbox_attachments,
        }
        write_json(case_workspace / "case_spec.json", prepared_case)
        prepared_cases.append(prepared_case)
        run_manifest.append(
            {
                "case_id": case["case_id"],
                "run_id": f"run-{case['case_id'].lower()}-{uuid.uuid4().hex[:8]}",
                "workspace_path": str(case_workspace.resolve()),
                "workspace_repo_relative_path": as_posix_relative(case_workspace, repo_root),
                "prompt_path": str((case_workspace / "prompt.txt").resolve()),
                "prompt_repo_relative_path": as_posix_relative(case_workspace / "prompt.txt", repo_root),
                "case_spec_repo_relative_path": as_posix_relative(case_workspace / "case_spec.json", repo_root),
                "attachment_count": len(sandbox_attachments),
                "status": "prepared_not_executed",
                "session_id": None,
                "turn_id": None,
            }
        )

    write_jsonl(output_dir / "prepared_cases.jsonl", prepared_cases)
    write_json(output_dir / "run_manifest.json", run_manifest)
    return run_manifest
