from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .common import ensure_dir, is_blankish, read_jsonl, write_json

CATEGORY_WEIGHTS = {"core": 0.45, "major": 0.45, "minor": 0.10}
JUDGEMENT_SCORES = {"pass": 1.0, "partial": 0.5, "fail": 0.0}


class D3JudgeClient(Protocol):
    def judge_rubric(self, *, query: str, reference_answer: str, final_response: str, rubric: dict[str, Any]) -> dict[str, Any]:
        ...


def is_d3_candidate(case: dict[str, Any]) -> bool:
    if "d3_candidate" in case:
        return bool(case.get("d3_candidate"))
    return not bool(case.get("d2_enabled"))


def d3_priority(case: dict[str, Any]) -> str:
    """Primary D3 for text-answer cases, secondary D3 for product/state cases."""
    if case.get("target_state"):
        return "secondary_to_d1"
    return "primary"


def normalize_rubrics(raw_rubrics: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_rubrics, list):
        return []
    grouped: dict[str, list[dict[str, Any]]] = {"core": [], "major": [], "minor": []}
    others: list[dict[str, Any]] = []
    rubrics: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rubrics, start=1):
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("rubric", "")).strip()
        if not text:
            continue
        category = str(raw.get("category", "minor")).strip().lower() or "minor"
        item = {
            "id": raw.get("id", len(rubrics) + 1),
            "category": category,
            "rubric": text,
            "rationale": str(raw.get("rationale", "")).strip(),
            "weight": raw.get("weight"),
        }
        rubrics.append(item)
        if item["weight"] is not None:
            continue
        if category in grouped:
            grouped[category].append(item)
        else:
            others.append(item)

    for category, total_weight in CATEGORY_WEIGHTS.items():
        items = grouped.get(category, [])
        if not items:
            continue
        per_item = total_weight / len(items)
        for item in items:
            item["weight"] = per_item
    if others:
        per_item = CATEGORY_WEIGHTS["minor"] / len(others)
        for item in others:
            item["weight"] = per_item
    for item in rubrics:
        if item["weight"] is None:
            item["weight"] = 0.0
    return rubrics


def build_d3_rubric_items(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for case in cases:
        if not is_d3_candidate(case):
            continue
        reference_answer = str(case.get("reference_answer", "")).strip()
        item = {
            "id": case["case_id"],
            "case_id": case["case_id"],
            "source_row": case.get("source_row"),
            "skill_name": case.get("skill_name", ""),
            "question": case.get("user_query", ""),
            "reference_answer": reference_answer,
            "images": [attachment["original_path"] for attachment in case.get("attachments", []) if str(attachment.get("mime_type", "")).startswith("image/")],
            "attachments": [
                {
                    "name": attachment.get("name"),
                    "relative_path": attachment.get("relative_path"),
                    "mime_type": attachment.get("mime_type"),
                    "sha256": attachment.get("sha256"),
                }
                for attachment in case.get("attachments", [])
            ],
            "d3_priority": d3_priority(case),
            "status": "ready_for_rubric_synthesis" if reference_answer else "blocked_by_missing_reference_answer",
            "rubrics": [],
        }
        items.append(item)
    return items


def write_d3_rubric_inputs(cases_path: Path, output_path: Path) -> list[dict[str, Any]]:
    items = build_d3_rubric_items(read_jsonl(cases_path))
    ensure_dir(output_path.parent)
    output_path.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return items


def _load_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix.lower() == ".jsonl":
        return read_jsonl(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []


def load_d3_rubrics(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    by_case: dict[str, dict[str, Any]] = {}
    for item in _load_json_or_jsonl(path):
        case_id = str(item.get("case_id") or item.get("id") or "").strip()
        if not case_id:
            continue
        rubrics = normalize_rubrics(item.get("rubrics"))
        if not rubrics:
            continue
        by_case[case_id] = {**item, "case_id": case_id, "rubrics": rubrics}
    return by_case


def parse_judge_json(text: str) -> dict[str, Any]:
    text = text.strip()
    candidates = [text]
    for match in re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL):
        candidates.append(match.strip())
    decoder = json.JSONDecoder()
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            return value if isinstance(value, dict) else {}
        except Exception:
            pass
        for index, char in enumerate(candidate):
            if char not in "{[":
                continue
            try:
                value, _ = decoder.raw_decode(candidate, index)
                return value if isinstance(value, dict) else {}
            except Exception:
                continue
    return {}


def normalize_judgement(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"pass", "passed", "true", "yes", "满足", "通过"}:
        return "pass"
    if text in {"partial", "partially_pass", "partially passed", "部分满足", "部分通过"}:
        return "partial"
    return "fail"


def build_d3_judge_prompt(*, query: str, reference_answer: str, final_response: str, rubric: dict[str, Any]) -> str:
    return f"""你是 EL Agent 自动评测中的 D3 答案质量裁判。

请只根据给定问题、参考答案、rubric 和候选回答，判断候选回答是否满足该 rubric。

评判规则：
1. 不要因为回答更长就加分。
2. 若候选回答与参考答案核心判断冲突，判 fail。
3. 若候选回答只覆盖部分要求，判 partial。
4. 若 rubric 涉及证据/数据，而候选回答无依据强行断言，判 fail。
5. 只输出 JSON，不要输出额外解释。

问题：
{query}

参考答案：
{reference_answer}

Rubric：
- category: {rubric.get("category", "")}
- weight: {rubric.get("weight", "")}
- rubric: {rubric.get("rubric", "")}
- rationale: {rubric.get("rationale", "")}

候选回答：
{final_response}

输出 JSON 格式：
{{
  "judgement": "pass | partial | fail",
  "reason": "...",
  "evidence_quote": "候选回答中最相关的原文片段；没有则为空字符串"
}}
"""


class OpenAICompatibleJudgeClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 120,
        temperature: float = 0.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature

    def judge_rubric(self, *, query: str, reference_answer: str, final_response: str, rubric: dict[str, Any]) -> dict[str, Any]:
        prompt = build_d3_judge_prompt(
            query=query,
            reference_answer=reference_answer,
            final_response=final_response,
            rubric=rubric,
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"D3 judge request failed: HTTP {exc.code}: {body}") from exc
        content = data["choices"][0]["message"]["content"]
        return parse_judge_json(content)


def make_judge_client_from_config(
    *,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
) -> OpenAICompatibleJudgeClient | None:
    base_url = base_url or os.environ.get("D3_JUDGE_BASE_URL")
    api_key = api_key or os.environ.get("D3_JUDGE_API_KEY")
    model = model or os.environ.get("D3_JUDGE_MODEL")
    if not base_url or not model:
        return None
    return OpenAICompatibleJudgeClient(base_url=base_url, api_key=api_key or "EMPTY", model=model)


def evaluate_d3(
    case: dict[str, Any],
    trajectory: dict[str, Any] | None,
    *,
    rubric_record: dict[str, Any] | None,
    judge_client: D3JudgeClient | None,
    d8_result: dict[str, Any] | None = None,
    pass_threshold: float = 0.75,
) -> dict[str, Any]:
    if not is_d3_candidate(case):
        return {"dimension": "D3", "status": "not_applicable", "score": None, "reason": "d2_enabled", "details": {}}
    if d8_result and d8_result.get("score") is not None and float(d8_result["score"]) < 0.5:
        return {
            "dimension": "D3",
            "status": "fail",
            "score": 0.0,
            "reason": "grounding_fail_precheck",
            "details": {"d8_score": d8_result.get("score")},
        }
    if not rubric_record:
        return {"dimension": "D3", "status": "blocked", "score": None, "reason": "blocked_by_missing_d3_rubrics", "details": {}}
    if not trajectory or is_blankish(trajectory.get("final_response", ""), treat_no_as_blank=False):
        return {"dimension": "D3", "status": "blocked", "score": None, "reason": "blocked_by_missing_final_response", "details": {}}
    if judge_client is None:
        return {
            "dimension": "D3",
            "status": "blocked",
            "score": None,
            "reason": "blocked_by_missing_judge_config",
            "details": {"rubric_count": len(rubric_record.get("rubrics", []))},
        }

    query = case.get("user_query", "")
    reference_answer = rubric_record.get("reference_answer") or case.get("reference_answer", "")
    final_response = trajectory.get("final_response", "")
    rubric_results: list[dict[str, Any]] = []
    weighted_score = 0.0
    total_weight = 0.0
    core_failed = False

    for rubric in rubric_record.get("rubrics", []):
        raw_judgement = judge_client.judge_rubric(
            query=query,
            reference_answer=reference_answer,
            final_response=final_response,
            rubric=rubric,
        )
        judgement = normalize_judgement(raw_judgement.get("judgement"))
        score = JUDGEMENT_SCORES[judgement]
        weight = float(rubric.get("weight", 0.0))
        total_weight += weight
        weighted_score += weight * score
        if rubric.get("category") == "core" and judgement == "fail":
            core_failed = True
        rubric_results.append(
            {
                "rubric_id": rubric.get("id"),
                "category": rubric.get("category"),
                "weight": weight,
                "rubric": rubric.get("rubric"),
                "judgement": judgement,
                "score": score,
                "reason": str(raw_judgement.get("reason", "")).strip(),
                "evidence_quote": str(raw_judgement.get("evidence_quote", "")).strip(),
            }
        )

    final_score = weighted_score / total_weight if total_weight > 0 else 0.0
    passed = not core_failed and final_score >= pass_threshold
    return {
        "dimension": "D3",
        "status": "pass" if passed else "fail",
        "score": final_score,
        "reason": "answer_quality_passed" if passed else ("core_rubric_failed" if core_failed else "weighted_score_below_threshold"),
        "details": {
            "pass_threshold": pass_threshold,
            "d3_priority": d3_priority(case),
            "rubric_results": rubric_results,
        },
    }


def write_d3_rubric_summary(rubrics_path: Path, output_path: Path) -> dict[str, Any]:
    rubrics = load_d3_rubrics(rubrics_path)
    summary = {
        "case_count_with_rubrics": len(rubrics),
        "rubric_count": sum(len(record.get("rubrics", [])) for record in rubrics.values()),
        "cases": [
            {
                "case_id": case_id,
                "rubric_count": len(record.get("rubrics", [])),
                "categories": sorted({rubric.get("category") for rubric in record.get("rubrics", [])}),
            }
            for case_id, record in sorted(rubrics.items())
        ],
    }
    write_json(output_path, summary)
    return summary
