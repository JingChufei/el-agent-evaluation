from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from el_eval_pipeline.agent_bundle import assert_agent_spec_is_sanitized
from el_eval_pipeline.parsers import parse_session_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _final_text(openclaw_result: dict[str, Any]) -> str:
    payloads = openclaw_result.get("payloads")
    if isinstance(payloads, list):
        texts = [str(item.get("text", "")).strip() for item in payloads if isinstance(item, dict)]
        texts = [text for text in texts if text]
        if texts:
            return "\n\n".join(texts)
    return str(openclaw_result.get("text") or openclaw_result.get("final_response") or "").strip()


def _build_message(prompt: str, case_spec: dict[str, Any]) -> str:
    attachments = case_spec.get("attachments") or []
    attachment_lines: list[str] = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        path = attachment.get("sandbox_path") or attachment.get("sandbox_relative_path")
        if path:
            attachment_lines.append(f"- {path}")

    if not attachment_lines:
        return prompt

    return (
        f"{prompt.rstrip()}\n\n"
        "本题附件已经放在当前评测 workspace 中。请按需读取这些文件：\n"
        + "\n".join(attachment_lines)
    )


def _parse_openclaw_json(output: str) -> dict[str, Any]:
    text = output.strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            parsed, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        if not text[start + end :].strip():
            return parsed
        candidates.append(parsed)

    for parsed in reversed(candidates):
        if "payloads" in parsed or "meta" in parsed:
            return parsed

    raise ValueError("openclaw did not return JSON")


def _session_snapshot(sessions_dir: Path) -> dict[Path, tuple[int, int]]:
    if not sessions_dir.exists():
        return {}
    snapshot: dict[Path, tuple[int, int]] = {}
    for path in sessions_dir.glob("*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _changed_session_files(before: dict[Path, tuple[int, int]], sessions_dir: Path) -> list[Path]:
    after = _session_snapshot(sessions_dir)
    changed = [
        path
        for path, state in after.items()
        if before.get(path) != state
    ]
    return sorted(changed, key=lambda path: path.stat().st_mtime_ns, reverse=True)


def _latest_session_turn(paths: list[Path]) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    errors: list[str] = []
    for path in paths:
        try:
            turns = parse_session_file(path)
        except Exception as exc:
            errors.append(f"{path}: {exc}")
            continue
        if turns:
            return turns[-1], path, None
    return None, None, "; ".join(errors) if errors else None


def _command_summary(command: list[str]) -> str:
    redacted: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("<message omitted>")
            skip_next = False
            continue
        redacted.append(item)
        if item == "--message":
            skip_next = True
    return " ".join(redacted)


def _default_sessions_dir(agent: str | None) -> Path:
    agent_id = agent or "main"
    return Path.home() / ".openclaw" / "agents" / agent_id / "sessions"


def run(args: argparse.Namespace) -> int:
    prompt = args.prompt.read_text(encoding="utf-8")
    case_spec = _load_json(args.case_spec)
    assert_agent_spec_is_sanitized(case_spec)
    case_id = str(case_spec.get("case_id") or os.environ.get("EL_EVAL_CASE_ID") or "unknown-case")
    run_id = str(os.environ.get("EL_EVAL_RUN_ID") or "")
    session_id = args.session_id or f"el-eval-{case_id}"
    message = _build_message(prompt, case_spec)

    command = [
        args.openclaw_bin,
        "agent",
        "--local",
        "--session-id",
        session_id,
        "--json",
        "--timeout",
        str(args.openclaw_timeout),
        "--message",
        message,
    ]
    if args.agent:
        command[3:3] = ["--agent", args.agent]

    sessions_dir = args.sessions_dir or _default_sessions_dir(args.agent)
    sessions_before = _session_snapshot(sessions_dir)
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=args.workspace,
        text=True,
        capture_output=True,
        timeout=args.process_timeout,
    )
    elapsed_seconds = time.time() - started

    raw_path = args.output.with_name("openclaw_response.json")
    stdout_path = args.output.with_name("openclaw_stdout.log")
    stderr_path = args.output.with_name("openclaw_stderr.log")
    stdout_path.write_text(completed.stdout, encoding="utf-8")
    stderr_path.write_text(completed.stderr, encoding="utf-8")

    openclaw_result: dict[str, Any] = {}
    parse_error = None
    parse_source = completed.stdout if completed.stdout.strip() else completed.stderr
    if parse_source.strip():
        try:
            openclaw_result = _parse_openclaw_json(parse_source)
            _write_json(raw_path, openclaw_result)
        except Exception as exc:
            parse_error = str(exc)

    session_files = _changed_session_files(sessions_before, sessions_dir)
    session_turn, session_trace_path, session_parse_error = _latest_session_turn(session_files)

    meta = openclaw_result.get("meta") if isinstance(openclaw_result.get("meta"), dict) else {}
    agent_meta = meta.get("agentMeta") if isinstance(meta.get("agentMeta"), dict) else {}
    prompt_report = meta.get("systemPromptReport") if isinstance(meta.get("systemPromptReport"), dict) else {}
    final_response = ""
    if session_turn:
        final_response = str(session_turn.get("final_response") or "").strip()
    if not final_response:
        final_response = _final_text(openclaw_result)
    model_calls = session_turn.get("model_calls", []) if session_turn else []
    if not model_calls:
        model_calls = [
            {
                "provider": agent_meta.get("provider"),
                "model": agent_meta.get("model"),
                "usage": agent_meta.get("lastCallUsage"),
                "duration_ms": meta.get("durationMs"),
            }
        ]

    agent_output = {
        "final_response": final_response,
        "session_id": (session_turn or {}).get("session_id") or prompt_report.get("sessionId") or agent_meta.get("sessionId") or session_id,
        "turn_id": f"{case_id}:{run_id or 'run'}",
        "steps": session_turn.get("steps", []) if session_turn else [],
        "tool_calls": session_turn.get("tool_calls", []) if session_turn else [],
        "tool_results": session_turn.get("tool_results", []) if session_turn else [],
        "model_calls": model_calls,
        "assistant_internal_text": session_turn.get("assistant_internal_text", "") if session_turn else "",
        "sandbox_intercept_log": [],
        "openclaw": {
            "command": _command_summary(command),
            "returncode": completed.returncode,
            "elapsed_seconds": elapsed_seconds,
            "raw_response_path": str(raw_path),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "parse_error": parse_error,
            "sessions_dir": str(sessions_dir),
            "changed_session_files": [str(path) for path in session_files],
            "session_trace_path": str(session_trace_path) if session_trace_path else None,
            "session_parse_error": session_parse_error,
        },
    }
    _write_json(args.output, agent_output)

    if completed.returncode != 0:
        print(completed.stderr or completed.stdout, file=sys.stderr)
        return completed.returncode
    if parse_error:
        print(parse_error, file=sys.stderr)
        return 2
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one EL evaluation case through OpenClaw.")
    parser.add_argument("--case-spec", type=Path, default=Path(os.environ.get("EL_EVAL_CASE_SPEC", "")))
    parser.add_argument("--prompt", type=Path, default=Path(os.environ.get("EL_EVAL_PROMPT_PATH", "")))
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("EL_EVAL_AGENT_OUTPUT", "agent_output.json")))
    parser.add_argument("--workspace", type=Path, default=Path(os.environ.get("EL_EVAL_WORKSPACE", ".")))
    parser.add_argument("--openclaw-bin", default="openclaw")
    parser.add_argument("--agent", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--sessions-dir", type=Path, default=None)
    parser.add_argument("--openclaw-timeout", type=int, default=600)
    parser.add_argument("--process-timeout", type=int, default=660)
    return parser


def main() -> None:
    raise SystemExit(run(build_parser().parse_args()))


if __name__ == "__main__":
    main()
