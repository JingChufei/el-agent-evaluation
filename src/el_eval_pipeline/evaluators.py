from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .common import is_blankish, read_jsonl, write_json, write_jsonl
from .d3 import evaluate_d3, load_d3_rubrics, make_judge_client_from_config


def _result(dimension: str, status: str, score: float | None = None, reason: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"dimension": dimension, "status": status, "score": score, "reason": reason, "details": details or {}}


def _trajectory_by_case(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    rows = read_jsonl(path) if path.is_file() else []
    return {row["case_id"]: row for row in rows if row.get("case_id")}


def evaluate_d1(case: dict[str, Any], trajectory: dict[str, Any] | None) -> dict[str, Any]:
    target = case.get("target_state")
    if not target:
        return _result("D1", "not_applicable", None, "no_target_state")
    if not trajectory:
        return _result("D1", "blocked", None, "blocked_by_missing_trajectory")
    final_files = trajectory.get("sandbox_final_files") or []
    if isinstance(final_files, list) and final_files:
        missing_from_snapshot: list[str] = []
        file_paths = [str(item.get("path", "")) for item in final_files if isinstance(item, dict)]
        file_names = {Path(path).name for path in file_paths}
        for required in target.get("required_files", []):
            pattern = re.sub(r"<[^>]+>", "*", required["path"])
            if not any(Path(path).match(pattern) or Path(path).name in {Path(pattern).name, pattern} for path in file_paths):
                if Path(pattern).name not in file_names:
                    missing_from_snapshot.append(pattern)
        if not missing_from_snapshot:
            return _result("D1", "pass", 1.0, "required_files_found_in_snapshot")
        total = max(len(target.get("required_files", [])), 1)
        return _result("D1", "fail", 1 - len(missing_from_snapshot) / total, "required_files_missing_in_snapshot", {"missing": missing_from_snapshot})
    workspace = trajectory.get("workspace_path") or trajectory.get("sandbox_final_snapshot_ref")
    if not workspace:
        return _result("D1", "blocked", None, "blocked_by_missing_workspace")
    workspace_path = Path(workspace)
    if not workspace_path.exists():
        return _result("D1", "blocked", None, "workspace_not_found", {"workspace_path": workspace})

    missing: list[str] = []
    for required in target.get("required_files", []):
        pattern = re.sub(r"<[^>]+>", "*", required["path"])
        if "/" in pattern or "\\" in pattern:
            matches = list(workspace_path.glob(pattern))
        else:
            matches = list(workspace_path.rglob(pattern))
        if not matches:
            matches = list(workspace_path.rglob(Path(pattern).name))
        if not matches:
            missing.append(pattern)
    if missing:
        total = max(len(target.get("required_files", [])), 1)
        return _result("D1", "fail", 1 - len(missing) / total, "required_files_missing", {"missing": missing})
    return _result("D1", "pass", 1.0, "required_files_found")


def _number_present(value: float, text: str, tolerance: dict[str, float]) -> bool:
    numbers = [float(match) for match in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]
    for number in numbers:
        abs_tol = tolerance.get("abs", 0.0)
        rel_tol = tolerance.get("rel", 0.0)
        if math.isclose(number, value, abs_tol=abs_tol, rel_tol=rel_tol):
            return True
    return False


def evaluate_d2(case: dict[str, Any], trajectory: dict[str, Any] | None) -> dict[str, Any]:
    if not case.get("d2_enabled"):
        return _result("D2", "not_applicable", None, "d2_disabled")
    expected = case.get("expected_answer")
    if not expected:
        return _result("D2", "blocked", None, "needs_annotation_fix")
    if not trajectory or is_blankish(trajectory.get("final_response", ""), treat_no_as_blank=False):
        return _result("D2", "blocked", None, "blocked_by_missing_final_response")
    response = trajectory.get("final_response", "")
    failures: list[dict[str, Any]] = []
    for assertion in expected.get("assertions", []):
        if assertion["type"] == "numeric_contains":
            if not _number_present(assertion["value"], response, assertion.get("tolerance", {})):
                failures.append(assertion)
        elif assertion["type"] == "text_contains":
            if assertion["value"] not in response:
                failures.append(assertion)
    if failures:
        total = max(len(expected.get("assertions", [])), 1)
        return _result("D2", "fail", 1 - len(failures) / total, "expected_answer_not_matched", {"failures": failures})
    return _result("D2", "pass", 1.0, "expected_answer_matched")


def evaluate_d4(case: dict[str, Any], trajectory: dict[str, Any] | None) -> dict[str, Any]:
    chain = case.get("gold_chain")
    if not chain:
        return _result("D4", "not_applicable", None, "no_gold_chain")
    if not trajectory:
        return _result("D4", "blocked", None, "blocked_by_missing_trajectory")
    step_names = [step.get("name") for step in trajectory.get("steps", []) if step.get("name")]
    tool_call_names = [call.get("name") for call in trajectory.get("tool_calls", []) if call.get("name")]
    all_names = set(step_names) | set(tool_call_names)
    missing: list[str] = []
    for stage in chain.get("stages", []):
        for required in stage.get("steps", []):
            if required not in all_names:
                missing.append(required)
    if missing:
        return _result("D4", "fail", 0.0, "gold_chain_steps_missing", {"missing": missing, "observed": sorted(all_names)})
    return _result("D4", "pass", 1.0, "gold_chain_covered")


def evaluate_d5(_: dict[str, Any], trajectory: dict[str, Any] | None) -> dict[str, Any]:
    if not trajectory:
        return _result("D5", "blocked", None, "blocked_by_missing_trajectory")
    errors: list[dict[str, Any]] = []
    for call in trajectory.get("tool_calls", []):
        raw = call.get("raw")
        args = call.get("args")
        if raw:
            try:
                json.loads(raw)
            except Exception as exc:
                errors.append({"tool_call": call.get("id"), "tool": call.get("name"), "error": "json_parse_error", "message": str(exc)})
        elif args is None:
            errors.append({"tool_call": call.get("id"), "tool": call.get("name"), "error": "missing_args"})
        if is_blankish(call.get("name", "")):
            errors.append({"tool_call": call.get("id"), "error": "missing_tool_name"})
    if errors:
        return _result("D5", "fail", 0.0, "tool_call_format_errors", {"errors": errors})
    return _result("D5", "pass", 1.0, "tool_calls_schema_parseable", {"tool_call_count": len(trajectory.get("tool_calls", []))})


def evaluate_d8(_: dict[str, Any], trajectory: dict[str, Any] | None) -> dict[str, Any]:
    if not trajectory:
        return _result("D8", "blocked", None, "blocked_by_missing_trajectory")
    response = trajectory.get("final_response", "")
    evidence = "\n".join(str(result.get("content", "")) for result in trajectory.get("tool_results", []))
    if not response:
        return _result("D8", "blocked", None, "blocked_by_missing_final_response")
    if not evidence:
        return _result("D8", "blocked", None, "blocked_by_missing_tool_evidence")
    claims = re.findall(r"[-+]?\d+(?:\.\d+)?%?|[A-Za-z][A-Za-z0-9_-]{2,}", response)
    claims = [claim for claim in claims if len(claim) >= 3]
    if not claims:
        return _result("D8", "not_applicable", None, "no_extractable_claims")
    hits = [claim for claim in claims if claim in evidence]
    score = len(hits) / len(claims)
    status = "pass" if score == 1 else "fail"
    return _result(status=status, dimension="D8", score=score, reason="grounding_key_value_match", details={"claim_count": len(claims), "hit_count": len(hits), "unhit": [claim for claim in claims if claim not in evidence][:50]})


def evaluate_cases(
    cases_path: Path,
    output_dir: Path,
    trajectories_path: Path | None = None,
    *,
    d3_rubrics_path: Path | None = None,
    d3_judge_base_url: str | None = None,
    d3_judge_api_key: str | None = None,
    d3_judge_model: str | None = None,
    d3_pass_threshold: float = 0.75,
) -> list[dict[str, Any]]:
    cases = read_jsonl(cases_path)
    trajectories = _trajectory_by_case(trajectories_path)
    d3_rubrics = load_d3_rubrics(d3_rubrics_path)
    d3_judge_client = make_judge_client_from_config(
        base_url=d3_judge_base_url,
        api_key=d3_judge_api_key,
        model=d3_judge_model,
    )
    rows: list[dict[str, Any]] = []
    for case in cases:
        trajectory = trajectories.get(case["case_id"])
        d8_result = evaluate_d8(case, trajectory)
        results = [
            evaluate_d1(case, trajectory),
            evaluate_d2(case, trajectory),
            evaluate_d3(
                case,
                trajectory,
                rubric_record=d3_rubrics.get(case["case_id"]),
                judge_client=d3_judge_client,
                d8_result=d8_result,
                pass_threshold=d3_pass_threshold,
            ),
            evaluate_d4(case, trajectory),
            evaluate_d5(case, trajectory),
            d8_result,
        ]
        rows.append(
            {
                "case_id": case["case_id"],
                "source_row": case["source_row"],
                "skill_name": case.get("skill_name", ""),
                "has_trajectory": trajectory is not None,
                "results": results,
            }
        )
    write_jsonl(output_dir / "evaluation_results.jsonl", rows)
    summary: dict[str, Any] = {"case_count": len(cases), "dimensions": {}}
    for dimension in ("D1", "D2", "D3", "D4", "D5", "D8"):
        statuses = [next(result for result in row["results"] if result["dimension"] == dimension)["status"] for row in rows]
        summary["dimensions"][dimension] = {status: statuses.count(status) for status in sorted(set(statuses))}
    write_json(output_dir / "evaluation_summary.json", summary)
    return rows
