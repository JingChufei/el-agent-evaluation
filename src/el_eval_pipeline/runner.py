from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from .common import ensure_dir, read_jsonl, sha256_file, write_json, write_jsonl
from .paths import as_posix_relative, resolve_repo_path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def _snapshot_workspace(workspace: Path, repo_root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    if not workspace.exists():
        return files
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            {
                "path": path.relative_to(workspace).as_posix(),
                "repo_relative_path": as_posix_relative(path, repo_root),
                "size_bytes": stat.st_size,
                "sha256": sha256_file(path),
                "mtime": stat.st_mtime,
            }
        )
    return files


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_agent_output(stdout: str, output_json: Path) -> dict[str, Any]:
    data = _read_optional_json(output_json)
    if data:
        return data
    for line in reversed(stdout.splitlines()):
        text = line.strip()
        if not text.startswith("{"):
            continue
        try:
            parsed = json.loads(text)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _render_command_template(template: str, values: dict[str, str]) -> str:
    command = template
    for key, value in values.items():
        command = command.replace("{" + key + "}", shlex.quote(value))
    return command


def _normalize_trajectory(
    *,
    case: dict[str, Any],
    manifest_row: dict[str, Any],
    workspace: Path,
    prompt: str,
    command: str,
    returncode: int,
    stdout: str,
    stderr: str,
    elapsed_seconds: float,
    agent_output: dict[str, Any],
    workspace_files_before: list[dict[str, Any]],
    workspace_files_after: list[dict[str, Any]],
    repo_root: Path,
) -> dict[str, Any]:
    final_response = str(agent_output.get("final_response") or agent_output.get("answer") or "").strip()
    return {
        "case_id": case["case_id"],
        "run_id": manifest_row.get("run_id"),
        "session_id": agent_output.get("session_id") or manifest_row.get("session_id"),
        "turn_id": agent_output.get("turn_id") or manifest_row.get("turn_id") or f"{case['case_id']}:turn-1",
        "user_query": case.get("user_query", prompt),
        "final_response": final_response,
        "workspace_path": str(workspace.resolve()),
        "workspace_repo_relative_path": as_posix_relative(workspace, repo_root),
        "attachments": case.get("attachments", []),
        "steps": agent_output.get("steps", []),
        "tool_calls": agent_output.get("tool_calls", []),
        "tool_results": agent_output.get("tool_results", []),
        "model_calls": agent_output.get("model_calls", []),
        "sandbox_initial_snapshot_ref": str(workspace.resolve()),
        "sandbox_final_snapshot_ref": str(workspace.resolve()),
        "sandbox_initial_files": workspace_files_before,
        "sandbox_final_files": workspace_files_after,
        "sandbox_intercept_log": agent_output.get("sandbox_intercept_log", []),
        "runner": {
            "command": command,
            "returncode": returncode,
            "elapsed_seconds": elapsed_seconds,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        },
        "status": "executed" if returncode == 0 else "agent_command_failed",
    }


def _manifest_rows(path: Path) -> list[dict[str, Any]]:
    data = _load_json(path)
    if not isinstance(data, list):
        raise ValueError(f"run manifest must be a JSON array: {path}")
    return [row for row in data if isinstance(row, dict)]


def run_agent_cases(
    *,
    manifest_path: Path,
    output_path: Path,
    command_template: str,
    repo_root: Path | None = None,
    case_ids: set[str] | None = None,
    stop_on_error: bool = False,
    timeout_seconds: int | None = None,
) -> list[dict[str, Any]]:
    repo_root = repo_root or Path.cwd()
    rows = _manifest_rows(manifest_path)
    trajectories: list[dict[str, Any]] = []

    for manifest_row in rows:
        case_id = str(manifest_row.get("case_id", ""))
        if case_ids and case_id not in case_ids:
            continue

        workspace = resolve_repo_path(manifest_row.get("workspace_repo_relative_path"), repo_root) or Path(manifest_row["workspace_path"])
        prompt_path = resolve_repo_path(manifest_row.get("prompt_repo_relative_path"), repo_root) or Path(manifest_row["prompt_path"])
        case_spec_path = resolve_repo_path(manifest_row.get("case_spec_repo_relative_path"), repo_root) or workspace / "case_spec.json"
        case = _load_json(case_spec_path)
        prompt = prompt_path.read_text(encoding="utf-8")
        agent_output_path = workspace / "agent_output.json"
        stdout_path = workspace / "agent_stdout.log"
        stderr_path = workspace / "agent_stderr.log"
        workspace_files_before = _snapshot_workspace(workspace, repo_root)

        env = os.environ.copy()
        env.update(
            {
                "EL_EVAL_CASE_ID": case_id,
                "EL_EVAL_RUN_ID": str(manifest_row.get("run_id") or ""),
                "EL_EVAL_WORKSPACE": str(workspace.resolve()),
                "EL_EVAL_PROMPT_PATH": str(prompt_path.resolve()),
                "EL_EVAL_CASE_SPEC": str(case_spec_path.resolve()),
                "EL_EVAL_AGENT_OUTPUT": str(agent_output_path.resolve()),
                "EL_EVAL_ATTACHMENTS_JSON": json.dumps(case.get("attachments", []), ensure_ascii=False),
            }
        )
        command = _render_command_template(
            command_template,
            {
                "case_id": case_id,
                "run_id": str(manifest_row.get("run_id") or ""),
                "workspace": str(workspace.resolve()),
                "prompt_path": str(prompt_path.resolve()),
                "case_spec": str(case_spec_path.resolve()),
                "agent_output": str(agent_output_path.resolve()),
            },
        )

        started = time.time()
        completed = subprocess.run(
            command,
            cwd=workspace,
            env=env,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        elapsed = time.time() - started
        _write_text(stdout_path, completed.stdout)
        _write_text(stderr_path, completed.stderr)
        agent_output = _parse_agent_output(completed.stdout, agent_output_path)
        workspace_files_after = _snapshot_workspace(workspace, repo_root)

        trajectory = _normalize_trajectory(
            case=case,
            manifest_row=manifest_row,
            workspace=workspace,
            prompt=prompt,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_seconds=elapsed,
            agent_output=agent_output,
            workspace_files_before=workspace_files_before,
            workspace_files_after=workspace_files_after,
            repo_root=repo_root,
        )
        trajectories.append(trajectory)
        if stop_on_error and completed.returncode != 0:
            break

    write_jsonl(output_path, trajectories)
    summary = {
        "trajectory_count": len(trajectories),
        "output_path": str(output_path),
        "status_counts": {
            status: sum(1 for row in trajectories if row.get("status") == status)
            for status in sorted({row.get("status") for row in trajectories})
        },
    }
    write_json(output_path.with_suffix(".summary.json"), summary)
    return trajectories
