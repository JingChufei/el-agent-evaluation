from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .common import read_jsonl, strip_think_blocks, write_jsonl


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(part for part in parts if part)
    return ""


def _content_tool_calls(content: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not isinstance(content, list):
        return calls
    for item in content:
        if item.get("type") == "toolCall":
            calls.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "args": item.get("arguments", {}),
                    "raw": json.dumps(item.get("arguments", {}), ensure_ascii=False),
                    "source": "session",
                }
            )
    return calls


def parse_session_file(path: Path) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    session_id = None
    trajectories: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    turn_index = 0

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        assistant_texts = current.pop("_assistant_texts")
        current["assistant_internal_text"] = "\n".join(assistant_texts).strip()
        current["final_response"] = strip_think_blocks(assistant_texts[-1]) if assistant_texts else ""
        current["steps"] = [
            *[
                {"type": "tool", "name": call.get("name"), "status": "called", "args": call.get("args", {})}
                for call in current["tool_calls"]
            ],
            *[
                {"type": "tool_result", "name": result.get("tool_name"), "status": result.get("status"), "args": {}}
                for result in current["tool_results"]
            ],
        ]
        trajectories.append(current)
        current = None

    for row in rows:
        if row.get("type") == "session":
            session_id = row.get("id")
            continue
        if row.get("type") != "message":
            continue
        message = row.get("message", {})
        role = message.get("role")
        if role == "user":
            flush()
            turn_index += 1
            current = {
                "case_id": None,
                "run_id": None,
                "session_id": session_id,
                "turn_id": f"{path.stem}:turn-{turn_index}",
                "source_session_file": str(path),
                "user_query": _content_text(message.get("content", [])),
                "attachments": [],
                "tool_calls": [],
                "tool_results": [],
                "model_calls": [],
                "steps": [],
                "sandbox_initial_snapshot_ref": None,
                "sandbox_final_snapshot_ref": None,
                "sandbox_intercept_log": [],
                "_assistant_texts": [],
            }
        elif role == "assistant":
            if current is None:
                continue
            text = _content_text(message.get("content", []))
            if text:
                current["_assistant_texts"].append(text)
            calls = _content_tool_calls(message.get("content", []))
            current["tool_calls"].extend(calls)
            if message.get("responseId") or message.get("model"):
                current["model_calls"].append(
                    {
                        "response_id": message.get("responseId"),
                        "provider": message.get("provider"),
                        "model": message.get("model"),
                        "api": message.get("api"),
                        "stop_reason": message.get("stopReason"),
                        "usage": message.get("usage", {}),
                    }
                )
        elif role == "toolResult":
            if current is None:
                continue
            details = message.get("details") or {}
            content = message.get("content")
            status = details.get("status") or ("error" if message.get("isError") else "ok")
            current["tool_results"].append(
                {
                    "tool_call_id": message.get("toolCallId"),
                    "tool_name": message.get("toolName"),
                    "content": _content_text(content) if isinstance(content, list) else content,
                    "raw_content": content,
                    "status": status,
                    "is_empty": not bool(_content_text(content) if isinstance(content, list) else content),
                    "details": details,
                }
            )
    flush()
    return trajectories


def parse_sessions_dir(sessions_dir: Path, output_path: Path) -> list[dict[str, Any]]:
    trajectories: list[dict[str, Any]] = []
    for path in sorted(sessions_dir.iterdir()):
        if path.is_file() and (path.name.endswith(".jsonl") or ".jsonl.reset" in path.name):
            trajectories.extend(parse_session_file(path))
    write_jsonl(output_path, trajectories)
    return trajectories


def parse_vllm_responses(path: Path, output_path: Path) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            grouped[row["req_id"]].append(row)

    calls: list[dict[str, Any]] = []
    for req_id, rows in grouped.items():
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reasons: list[str] = []
        response_ids: set[str] = set()
        for row in rows:
            chunk_text = row.get("chunk_text", "")
            if not chunk_text.startswith("data: "):
                continue
            payload_text = chunk_text[len("data: ") :].strip()
            if payload_text == "[DONE]":
                continue
            payload = json.loads(payload_text)
            if payload.get("id"):
                response_ids.add(payload["id"])
            choice = (payload.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                content_parts.append(delta["content"])
            for call_delta in delta.get("tool_calls") or []:
                index = int(call_delta.get("index", len(tool_calls)))
                current = tool_calls.setdefault(index, {"id": "", "type": "", "function": {"name": "", "arguments": ""}})
                if call_delta.get("id"):
                    current["id"] += call_delta["id"]
                if call_delta.get("type"):
                    current["type"] += call_delta["type"]
                function_delta = call_delta.get("function") or {}
                if function_delta.get("name"):
                    current["function"]["name"] += function_delta["name"]
                if function_delta.get("arguments"):
                    current["function"]["arguments"] += function_delta["arguments"]
            if choice.get("finish_reason"):
                finish_reasons.append(choice["finish_reason"])
        calls.append(
            {
                "req_id": req_id,
                "response_ids": sorted(response_ids),
                "first_ts": rows[0].get("ts"),
                "last_ts": rows[-1].get("ts"),
                "chunk_count": len(rows),
                "content": "".join(content_parts),
                "final_response": strip_think_blocks("".join(content_parts)),
                "tool_calls": list(tool_calls.values()),
                "finish_reasons": finish_reasons,
            }
        )
    write_jsonl(output_path, calls)
    return calls

