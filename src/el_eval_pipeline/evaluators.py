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


def _reference_artifact_details(case: dict[str, Any]) -> dict[str, Any]:
    artifacts = case.get("reference_artifacts") or []
    if not artifacts:
        return {}
    return {
        "reference_artifact_count": len(artifacts),
        "reference_artifacts": [
            {
                "relative_path": artifact.get("relative_path"),
                "role": artifact.get("role"),
                "kind": artifact.get("kind"),
                "description": artifact.get("description"),
                "sha256": artifact.get("sha256"),
            }
            for artifact in artifacts
            if isinstance(artifact, dict)
        ],
        "needs_artifact_quality_review": True,
    }


def _required_file_pattern(required_path: str) -> str:
    return re.sub(r"<[^>]+>", "*", required_path)


def _path_matches_required(path: str, pattern: str) -> bool:
    return Path(path).match(pattern) or Path(path).name in {Path(pattern).name, pattern}


def _required_file_text_candidates(pattern: str) -> list[str]:
    candidates = [pattern, Path(pattern).name]
    if "*" in pattern:
        parts = [part for part in re.split(r"[*\\/]+", pattern) if part]
        candidates.extend(parts)
    return [candidate for candidate in dict.fromkeys(candidates) if candidate and candidate != "*"]


def _trajectory_text_evidence(trajectory: dict[str, Any]) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for key in ("steps", "tool_calls", "tool_results"):
        value = trajectory.get(key)
        if not isinstance(value, list):
            continue
        for index, item in enumerate(value):
            if isinstance(item, dict):
                items.append({"source": f"{key}[{index}]", "text": json.dumps(item, ensure_ascii=False)})
    final_response = str(trajectory.get("final_response", "")).strip()
    if final_response:
        items.append({"source": "final_response", "text": final_response})
    return items


def _match_required_file_in_text(pattern: str, evidence_items: list[dict[str, str]]) -> dict[str, Any] | None:
    candidates = _required_file_text_candidates(pattern)
    for item in evidence_items:
        text = item["text"]
        if any(candidate in text for candidate in candidates):
            return {"source": item["source"], "evidence": text[:500]}
    return None


def _match_required_files(
    required_files: list[dict[str, Any]],
    *,
    final_files: list[Any] | None = None,
    evidence_items: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    final_files = final_files or []
    evidence_items = evidence_items or []
    file_paths = [str(item.get("path", "")) for item in final_files if isinstance(item, dict)]
    file_names = {Path(path).name for path in file_paths}
    matched: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for required in required_files:
        pattern = _required_file_pattern(str(required.get("path", "")))
        snapshot_match = next((path for path in file_paths if _path_matches_required(path, pattern)), None)
        if snapshot_match or Path(pattern).name in file_names:
            matched[pattern] = {"source": "sandbox_final_files", "evidence": snapshot_match or Path(pattern).name}
            continue
        text_match = _match_required_file_in_text(pattern, evidence_items)
        if text_match:
            matched[pattern] = text_match
            continue
        missing.append(pattern)
    total = len(required_files)
    score = len(matched) / total if total else None
    return {"matched": matched, "missing": missing, "score": score}


def evaluate_d1(case: dict[str, Any], trajectory: dict[str, Any] | None) -> dict[str, Any]:
    target = case.get("target_state")
    if not target:
        return _result("D1", "not_applicable", None, "no_target_state")
    reference_details = _reference_artifact_details(case)
    if not trajectory:
        return _result("D1", "blocked", None, "blocked_by_missing_trajectory", reference_details)
    required_files = target.get("required_files", [])
    evidence_items = _trajectory_text_evidence(trajectory)
    final_files = trajectory.get("sandbox_final_files") or []
    if isinstance(final_files, list) and final_files:
        match_result = _match_required_files(required_files, final_files=final_files, evidence_items=evidence_items)
        if not match_result["missing"]:
            return _result("D1", "pass", 1.0, "required_files_found", {**match_result, **reference_details})
        return _result(
            "D1",
            "fail",
            match_result["score"],
            "required_files_missing",
            {**match_result, **reference_details},
        )
    workspace = trajectory.get("workspace_path") or trajectory.get("sandbox_final_snapshot_ref")
    if not workspace:
        match_result = _match_required_files(required_files, evidence_items=evidence_items)
        if match_result["matched"]:
            status = "pass" if not match_result["missing"] else "fail"
            reason = "required_files_found_in_tool_evidence" if status == "pass" else "required_files_missing"
            return _result("D1", status, match_result["score"], reason, {**match_result, **reference_details})
        return _result("D1", "blocked", None, "blocked_by_missing_workspace", reference_details)
    workspace_path = Path(workspace)
    if not workspace_path.exists():
        match_result = _match_required_files(required_files, evidence_items=evidence_items)
        if match_result["matched"]:
            status = "pass" if not match_result["missing"] else "fail"
            reason = "required_files_found_in_tool_evidence" if status == "pass" else "required_files_missing"
            return _result("D1", status, match_result["score"], reason, {**match_result, "workspace_path": workspace, **reference_details})
        return _result("D1", "blocked", None, "workspace_not_found", {"workspace_path": workspace, **reference_details})

    synthetic_final_files: list[dict[str, str]] = []
    for required in required_files:
        pattern = _required_file_pattern(required["path"])
        if "/" in pattern or "\\" in pattern:
            matches = list(workspace_path.glob(pattern))
        else:
            matches = list(workspace_path.rglob(pattern))
        if not matches:
            matches = list(workspace_path.rglob(Path(pattern).name))
        synthetic_final_files.extend({"path": str(match.relative_to(workspace_path))} for match in matches)
    match_result = _match_required_files(required_files, final_files=synthetic_final_files, evidence_items=evidence_items)
    if match_result["missing"]:
        return _result("D1", "fail", match_result["score"], "required_files_missing", {**match_result, **reference_details})
    return _result("D1", "pass", 1.0, "required_files_found", {**match_result, **reference_details})


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
        elif assertion["type"] == "text_contains_any":
            if not any(value in response for value in assertion.get("values", [])):
                failures.append(assertion)
        elif assertion["type"] == "text_contains_all":
            if not all(value in response for value in assertion.get("values", [])):
                failures.append(assertion)
        else:
            failures.append({**assertion, "error": "unsupported_assertion_type"})
    if failures:
        total = max(len(expected.get("assertions", [])), 1)
        return _result("D2", "fail", 1 - len(failures) / total, "expected_answer_not_matched", {"failures": failures})
    return _result("D2", "pass", 1.0, "expected_answer_matched")


def _match_d4_required(
    required_items: list[str],
    *,
    all_names: set[str],
    evidence_items: list[dict[str, Any]],
) -> dict[str, Any]:
    missing: list[str] = []
    matched: dict[str, dict[str, Any]] = {}
    for required in required_items:
        required_text = str(required).strip()
        if not required_text:
            continue
        if required_text in all_names:
            matched[required_text] = {"source": "name", "evidence": required_text}
            continue
        required_lower = required_text.lower()
        evidence = next((item for item in evidence_items if required_lower in item["text"].lower()), None)
        if evidence:
            matched[required_text] = {
                "source": evidence["source"],
                "tool_name": evidence.get("name"),
                "evidence": evidence["text"][:500],
            }
        else:
            missing.append(required_text)
    total = len(matched) + len(missing)
    score = len(matched) / total if total else None
    status = "not_applicable" if total == 0 else ("pass" if not missing else "fail")
    return {
        "status": status,
        "score": score,
        "required_count": total,
        "matched": matched,
        "missing": missing,
    }


def _weighted_d4_score(skill_eval: dict[str, Any], tool_eval: dict[str, Any]) -> float | None:
    weighted_score = 0.0
    active_weight = 0.0
    for sub_eval in (skill_eval, tool_eval):
        if sub_eval["score"] is None:
            continue
        weighted_score += 0.5 * float(sub_eval["score"])
        active_weight += 0.5
    return weighted_score / active_weight if active_weight else None


def evaluate_d4(case: dict[str, Any], trajectory: dict[str, Any] | None) -> dict[str, Any]:
    chain = case.get("gold_chain")
    if not chain:
        return _result("D4", "not_applicable", None, "no_gold_chain")
    if not trajectory:
        return _result("D4", "blocked", None, "blocked_by_missing_trajectory")
    steps = [step for step in trajectory.get("steps", []) if isinstance(step, dict)]
    tool_calls = [call for call in trajectory.get("tool_calls", []) if isinstance(call, dict)]
    step_names = [str(step.get("name")) for step in steps if step.get("name")]
    tool_call_names = [str(call.get("name")) for call in tool_calls if call.get("name")]
    all_names = set(step_names) | set(tool_call_names)
    evidence_items = [
        {"source": "step", "name": step.get("name"), "text": json.dumps(step, ensure_ascii=False)}
        for step in steps
    ] + [
        {"source": "tool_call", "name": call.get("name"), "text": json.dumps(call, ensure_ascii=False)}
        for call in tool_calls
    ]
    required_by_type: dict[str, list[str]] = {"skill": [], "tool": []}
    for stage in chain.get("stages", []):
        step_type = str(stage.get("step_type") or "tool").strip().lower()
        bucket = "skill" if step_type == "skill" else "tool"
        for required in stage.get("steps", []):
            required_text = str(required).strip()
            if required_text:
                required_by_type[bucket].append(required_text)
    skill_eval = _match_d4_required(required_by_type["skill"], all_names=all_names, evidence_items=evidence_items)
    tool_eval = _match_d4_required(required_by_type["tool"], all_names=all_names, evidence_items=evidence_items)
    missing = [*skill_eval["missing"], *tool_eval["missing"]]
    overall_score = _weighted_d4_score(skill_eval, tool_eval)
    details = {
        "skills": skill_eval,
        "tools": tool_eval,
        "missing": missing,
        "matched": {**skill_eval["matched"], **tool_eval["matched"]},
        "observed_names": sorted(all_names),
    }
    if missing:
        return _result(
            "D4",
            "fail",
            overall_score,
            "gold_chain_steps_missing",
            details,
        )
    return _result("D4", "pass", overall_score, "gold_chain_covered", details)


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


def _score_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(result["score"]) for result in results if result.get("score") is not None]
    if not scores:
        return {"scored_count": 0, "average": None, "min": None, "max": None}
    return {
        "scored_count": len(scores),
        "average": sum(scores) / len(scores),
        "min": min(scores),
        "max": max(scores),
    }


def _nested_score_summary(results: list[dict[str, Any]], *path: str) -> dict[str, Any]:
    nested_results: list[dict[str, Any]] = []
    for result in results:
        value: Any = result
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        if isinstance(value, dict):
            nested_results.append(value)
    return _score_summary(nested_results)


def evaluate_cases(
    cases_path: Path,
    output_dir: Path,
    trajectories_path: Path | None = None,
    *,
    d3_rubrics_path: Path | None = None,
    d3_judge_base_url: str | None = None,
    d3_judge_api_key: str | None = None,
    d3_judge_model: str | None = None,
    d3_judge_timeout_seconds: int | None = None,
    d3_judge_max_tokens: int | None = None,
    d3_pass_threshold: float = 0.75,
) -> list[dict[str, Any]]:
    cases = read_jsonl(cases_path)
    trajectories = _trajectory_by_case(trajectories_path)
    d3_rubrics = load_d3_rubrics(d3_rubrics_path)
    d3_judge_client = make_judge_client_from_config(
        base_url=d3_judge_base_url,
        api_key=d3_judge_api_key,
        model=d3_judge_model,
        timeout=d3_judge_timeout_seconds,
        max_tokens=d3_judge_max_tokens,
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
        dimension_results = [next(result for result in row["results"] if result["dimension"] == dimension) for row in rows]
        statuses = [result["status"] for result in dimension_results]
        status_counts = {status: statuses.count(status) for status in sorted(set(statuses))}
        dimension_summary: dict[str, Any] = {
            **status_counts,
            "statuses": status_counts,
            "scores": _score_summary(dimension_results),
        }
        if dimension == "D4":
            dimension_summary["subscores"] = {
                "skills": _nested_score_summary(dimension_results, "details", "skills"),
                "tools": _nested_score_summary(dimension_results, "details", "tools"),
            }
        summary["dimensions"][dimension] = dimension_summary
    write_json(output_dir / "evaluation_summary.json", summary)
    return rows
