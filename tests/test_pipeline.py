from __future__ import annotations

import json
from pathlib import Path

from el_eval_pipeline.attachments import build_attachment_manifest
from el_eval_pipeline.d3 import build_d3_rubric_items, evaluate_d3, normalize_rubrics
from el_eval_pipeline.evaluators import evaluate_cases
from el_eval_pipeline.parsers import parse_session_file, parse_vllm_responses
from el_eval_pipeline.preprocess import load_cases_from_excel
from el_eval_pipeline.runner import run_agent_cases
from el_eval_pipeline.workspaces import prepare_case_workspaces

ROOT = Path(__file__).resolve().parents[1]


def test_filtered_dataset_preprocesses_35_cases_and_resolves_attachments() -> None:
    cases, manifest, errors = load_cases_from_excel(
        ROOT / "EL Agent测试集_260529_筛选后.xlsx",
        ROOT / "测试集相关文件",
    )
    assert len(cases) == 35
    assert cases[0]["case_id"] == "EL260529F-0001"
    assert cases[0]["source_row"] == 3
    assert len([case for case in cases if case["input_file_cell"]]) == 12
    assert not errors
    assert len(manifest) == 14
    assert all(not entry["basename"].startswith("~$") for entry in manifest)
    assert cases[0]["attachments"][0]["name"] == "EL260529F-0001 输入图片.png"
    assert cases[0]["attachments"][0]["mime_type"] == "image/png"
    first_assertions = cases[0]["expected_answer"]["assertions"]
    assert first_assertions[0]["type"] == "numeric_contains"
    assert first_assertions[0]["label"] == "HOMO"
    assert first_assertions[0]["value"] == -5.4
    d3_candidates = [case for case in cases if case["d3_candidate"]]
    assert len(d3_candidates) == 19
    assert all("D3" in case["evaluable_dimensions"] for case in d3_candidates)


def test_multi_csv_attachment_order_is_preserved() -> None:
    cases, _, _ = load_cases_from_excel(
        ROOT / "EL Agent测试集_260529_筛选后.xlsx",
        ROOT / "测试集相关文件",
    )
    lt97_case = next(case for case in cases if case["source_row"] == 21)
    names = [attachment["name"] for attachment in lt97_case["attachments"]]
    assert names == [
        "CH166-HR-CIC2-260331-HRF494WCJ094 030031-XBY-260401-6-1-CC0.79mA.csv",
        "CH167-HR-CIC2-260331-HRF494WCJ094 030031-XBY-260401-7-3-CC0.79mA.csv",
        "CH168-HR-CIC2-260331-HRF494WCJ094 030031-XBY-260401-8-1-CC0.79mA.csv",
    ]


def test_attachment_manifest_preflights_main_file_types() -> None:
    manifest = build_attachment_manifest(ROOT / "测试集相关文件")
    by_name = {entry["basename"]: entry for entry in manifest}
    assert by_name["TE-SHB-96测试原始数据.xlsx"]["preflight"]["readable"] is True
    assert by_name["EML膜层厚度对比实验.pptx"]["preflight"]["slides"] >= 1
    assert by_name["印刷蓝光OLED材料项目技术开发报告.docx"]["preflight"]["readable"] is True


def test_session_parser_groups_tool_calls_and_results(tmp_path: Path) -> None:
    session_path = tmp_path / "sample.jsonl"
    records = [
        {"type": "session", "id": "s1"},
        {"type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]}},
        {
            "type": "message",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "calling"},
                    {"type": "toolCall", "id": "read:0", "name": "read", "arguments": {"path": "x.txt"}},
                ],
                "responseId": "r1",
                "model": "m",
            },
        },
        {
            "type": "message",
            "message": {
                "role": "toolResult",
                "toolCallId": "read:0",
                "toolName": "read",
                "content": [{"type": "text", "text": "ok"}],
                "details": {"status": "ok"},
            },
        },
        {"type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "</think>\n\nfinal"}]}},
    ]
    session_path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records), encoding="utf-8")
    trajectories = parse_session_file(session_path)
    assert len(trajectories) == 1
    trajectory = trajectories[0]
    assert trajectory["session_id"] == "s1"
    assert trajectory["tool_calls"][0]["name"] == "read"
    assert trajectory["tool_results"][0]["content"] == "ok"
    assert trajectory["final_response"] == "final"


def test_vllm_parser_reconstructs_first_tool_call(tmp_path: Path) -> None:
    output = tmp_path / "vllm.jsonl"
    calls = parse_vllm_responses(ROOT / "openclaw_vllm_responses.jsonl", output)
    first = calls[0]
    assert first["req_id"] == "0ee39166-c935-4611-ad3c-c675bdcc414a"
    assert first["finish_reasons"] == ["tool_calls"]
    assert first["tool_calls"][0]["function"]["name"] == "read"
    assert "feifeijing" in first["tool_calls"][0]["function"]["arguments"]


def test_d3_rubric_inputs_and_weight_normalization() -> None:
    cases, _, _ = load_cases_from_excel(
        ROOT / "EL Agent测试集_260529_筛选后.xlsx",
        ROOT / "测试集相关文件",
    )
    items = build_d3_rubric_items(cases)
    assert len(items) == 19
    assert all(item["status"] == "ready_for_rubric_synthesis" for item in items)
    rubrics = normalize_rubrics(
        [
            {"category": "core", "rubric": "核心结论正确", "rationale": "回应问题"},
            {"category": "major", "rubric": "解释关键机制", "rationale": "支撑结论"},
            {"category": "minor", "rubric": "表达清晰", "rationale": "提升可读性"},
        ]
    )
    weights = {rubric["category"]: rubric["weight"] for rubric in rubrics}
    assert weights == {"core": 0.45, "major": 0.45, "minor": 0.10}


class _FakeD3Judge:
    def judge_rubric(self, *, query: str, reference_answer: str, final_response: str, rubric: dict) -> dict:
        if "必须失败" in rubric["rubric"]:
            return {"judgement": "fail", "reason": "missing", "evidence_quote": ""}
        return {"judgement": "pass", "reason": "covered", "evidence_quote": final_response[:20]}


def test_d3_evaluator_scores_rubrics_and_core_failure_blocks_pass() -> None:
    case = {
        "case_id": "c1",
        "d2_enabled": False,
        "user_query": "请分析原因",
        "reference_answer": "需要给出核心结论和机制。",
        "target_state": None,
    }
    trajectory = {"case_id": "c1", "final_response": "核心结论正确，并解释关键机制。"}
    rubric_record = {
        "case_id": "c1",
        "reference_answer": case["reference_answer"],
        "rubrics": normalize_rubrics(
            [
                {"id": 1, "category": "core", "rubric": "核心结论正确", "rationale": ""},
                {"id": 2, "category": "major", "rubric": "解释关键机制", "rationale": ""},
            ]
        ),
    }
    passed = evaluate_d3(case, trajectory, rubric_record=rubric_record, judge_client=_FakeD3Judge())
    assert passed["status"] == "pass"
    assert passed["score"] == 1.0

    rubric_record["rubrics"][0]["rubric"] = "必须失败"
    failed = evaluate_d3(case, trajectory, rubric_record=rubric_record, judge_client=_FakeD3Judge())
    assert failed["status"] == "fail"
    assert failed["reason"] == "core_rubric_failed"


def test_d3_evaluate_cases_blocks_without_judge_config(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.jsonl"
    trajectories_path = tmp_path / "trajectories.jsonl"
    rubrics_path = tmp_path / "fixed_d3_rubrics.json"
    output_dir = tmp_path / "out"

    case = {
        "case_id": "c1",
        "source_row": 3,
        "skill_name": "analysis",
        "d2_enabled": False,
        "d3_candidate": True,
        "user_query": "请分析原因",
        "reference_answer": "需要给出核心结论。",
        "target_state": None,
        "gold_chain": None,
    }
    trajectory = {
        "case_id": "c1",
        "final_response": "核心结论正确。",
        "tool_calls": [],
        "tool_results": [],
        "steps": [],
    }
    rubrics = [
        {
            "case_id": "c1",
            "reference_answer": case["reference_answer"],
            "rubrics": [{"id": 1, "category": "core", "rubric": "核心结论正确", "rationale": ""}],
        }
    ]
    cases_path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    trajectories_path.write_text(json.dumps(trajectory, ensure_ascii=False) + "\n", encoding="utf-8")
    rubrics_path.write_text(json.dumps(rubrics, ensure_ascii=False), encoding="utf-8")

    rows = evaluate_cases(cases_path, output_dir, trajectories_path, d3_rubrics_path=rubrics_path)
    d3_result = next(result for result in rows[0]["results"] if result["dimension"] == "D3")
    summary = json.loads((output_dir / "evaluation_summary.json").read_text(encoding="utf-8"))

    assert d3_result["status"] == "blocked"
    assert d3_result["reason"] == "blocked_by_missing_judge_config"
    assert d3_result["details"]["rubric_count"] == 1
    assert summary["dimensions"]["D3"]["blocked"] == 1


def test_run_agent_cases_uses_external_command_and_writes_trajectory(tmp_path: Path) -> None:
    attachment = tmp_path / "input.txt"
    attachment.write_text("attachment", encoding="utf-8")
    cases_path = tmp_path / "cases.jsonl"
    output_dir = tmp_path / "out"
    case = {
        "case_id": "c1",
        "source_row": 1,
        "user_query": "hello",
        "attachments": [
            {
                "name": "input.txt",
                "original_relative_path": "input.txt",
                "original_path": str(attachment),
                "mime_type": "text/plain",
                "sha256": "not-used",
            }
        ],
    }
    cases_path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")
    prepare_case_workspaces(cases_path, output_dir, repo_root=tmp_path, copy_attachments=False, portable_paths=True)
    assert not (output_dir / "workspaces" / "c1" / "inputs").exists()

    fake_agent = tmp_path / "fake_agent.py"
    fake_agent.write_text(
        "\n".join(
            [
                "import json, os",
                "out = os.environ['EL_EVAL_AGENT_OUTPUT']",
                "workspace = os.environ['EL_EVAL_WORKSPACE']",
                "open(os.path.join(workspace, 'result.txt'), 'w', encoding='utf-8').write('done')",
                "json.dump({'final_response': 'agent final', 'tool_calls': [{'id': 't1', 'name': 'read', 'args': {}}], 'tool_results': [{'tool_call_id': 't1', 'tool_name': 'read', 'content': 'ok'}]}, open(out, 'w', encoding='utf-8'), ensure_ascii=False)",
            ]
        ),
        encoding="utf-8",
    )

    trajectories = run_agent_cases(
        manifest_path=output_dir / "run_manifest.json",
        output_path=output_dir / "trajectories.jsonl",
        command_template=f"python3 {fake_agent}",
        repo_root=tmp_path,
    )

    assert len(trajectories) == 1
    trajectory = trajectories[0]
    assert trajectory["case_id"] == "c1"
    assert trajectory["final_response"] == "agent final"
    assert trajectory["status"] == "executed"
    assert any(item["path"] == "inputs/input.txt" for item in trajectory["sandbox_initial_files"])
    assert any(item["path"] == "result.txt" for item in trajectory["sandbox_final_files"])
    assert (output_dir / "trajectories.jsonl").exists()
