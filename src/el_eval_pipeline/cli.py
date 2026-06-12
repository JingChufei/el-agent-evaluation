from __future__ import annotations

import argparse
from pathlib import Path

from .agent_bundle import prepare_agent_execution_bundle
from .d3 import write_d3_rubric_inputs, write_d3_rubric_summary
from .evaluators import evaluate_cases
from .parsers import parse_sessions_dir, parse_vllm_responses
from .preprocess import preprocess_dataset
from .runner import run_agent_cases
from .workspaces import prepare_case_workspaces

DEFAULT_EXCEL = Path("EL Agent测试集_260529_筛选后.xlsx")
DEFAULT_ATTACHMENTS = Path("测试集相关文件")
DEFAULT_OUTPUT = Path("outputs/pipeline")
DEFAULT_BUNDLE = Path("execution_bundle")
DEFAULT_AGENT_BUNDLE = Path("agent_execution_bundle")


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--excel", type=Path, default=DEFAULT_EXCEL)
    parser.add_argument("--attachments", type=Path, default=DEFAULT_ATTACHMENTS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skills-workbook", type=Path, default=Path("EL agent 专业技能清单.xlsx"))
    parser.add_argument("--sessions-json", type=Path, default=Path("sessions/sessions.json"))


def cmd_preprocess(args: argparse.Namespace) -> None:
    result = preprocess_dataset(
        args.excel,
        args.attachments,
        args.output_dir,
        skills_workbook=args.skills_workbook if args.skills_workbook.exists() else None,
        sessions_json=args.sessions_json if args.sessions_json.exists() else None,
    )
    print(f"wrote {len(result['cases'])} cases to {args.output_dir}")
    print(f"attachment_count={len(result['attachment_manifest'])}")
    print(f"d2_missing_answer_count={result['quality_report']['d2_missing_answer_count']}")


def cmd_prepare_workspaces(args: argparse.Namespace) -> None:
    manifest = prepare_case_workspaces(args.cases, args.output_dir)
    print(f"prepared {len(manifest)} case workspaces under {args.output_dir / 'workspaces'}")


def cmd_parse_sessions(args: argparse.Namespace) -> None:
    trajectories = parse_sessions_dir(args.sessions_dir, args.output)
    print(f"parsed {len(trajectories)} session turns to {args.output}")


def cmd_parse_vllm(args: argparse.Namespace) -> None:
    calls = parse_vllm_responses(args.responses, args.output)
    print(f"parsed {len(calls)} vLLM model calls to {args.output}")


def cmd_evaluate(args: argparse.Namespace) -> None:
    rows = evaluate_cases(
        args.cases,
        args.output_dir,
        args.trajectories,
        d3_rubrics_path=args.d3_rubrics,
        d3_judge_base_url=args.d3_judge_base_url,
        d3_judge_api_key=args.d3_judge_api_key,
        d3_judge_model=args.d3_judge_model,
        d3_pass_threshold=args.d3_pass_threshold,
    )
    print(f"evaluated {len(rows)} cases to {args.output_dir}")


def cmd_prepare_d3_rubric_inputs(args: argparse.Namespace) -> None:
    items = write_d3_rubric_inputs(args.cases, args.output)
    ready = sum(1 for item in items if item.get("status") == "ready_for_rubric_synthesis")
    print(f"wrote {len(items)} D3 rubric input items to {args.output}; ready={ready}")


def cmd_summarize_d3_rubrics(args: argparse.Namespace) -> None:
    summary = write_d3_rubric_summary(args.d3_rubrics, args.output)
    print(
        f"summarized D3 rubrics: cases={summary['case_count_with_rubrics']} "
        f"rubrics={summary['rubric_count']} -> {args.output}"
    )


def cmd_prepare_execution_bundle(args: argparse.Namespace) -> None:
    result = preprocess_dataset(
        args.excel,
        args.attachments,
        args.bundle_dir,
        skills_workbook=args.skills_workbook if args.skills_workbook.exists() else None,
        sessions_json=args.sessions_json if args.sessions_json.exists() else None,
        portable_paths=True,
    )
    cases_path = args.bundle_dir / "cases.jsonl"
    manifest = prepare_case_workspaces(
        cases_path,
        args.bundle_dir,
        copy_attachments=False,
        portable_paths=True,
        stable_run_ids=True,
    )
    write_d3_rubric_inputs(cases_path, args.bundle_dir / "d3_rubric_inputs.json")
    print(
        f"prepared portable execution bundle under {args.bundle_dir}; "
        f"cases={len(result['cases'])} runs={len(manifest)}"
    )


def cmd_prepare_agent_execution_bundle(args: argparse.Namespace) -> None:
    report = prepare_agent_execution_bundle(
        source_bundle_dir=args.source_bundle_dir,
        output_dir=args.output_dir,
        include_code=not args.no_code,
    )
    print(
        f"prepared GT-free agent execution bundle under {args.output_dir}; "
        f"cases={report['case_count']} attachments={report['attachment_count']} leak_scan={report['status']}"
    )


def cmd_run_agent(args: argparse.Namespace) -> None:
    case_ids = set(args.case_id or []) or None
    trajectories = run_agent_cases(
        manifest_path=args.manifest,
        output_path=args.output,
        command_template=args.command,
        case_ids=case_ids,
        stop_on_error=args.stop_on_error,
        timeout_seconds=args.timeout_seconds,
    )
    print(f"ran {len(trajectories)} agent cases -> {args.output}")


def cmd_run_all(args: argparse.Namespace) -> None:
    cmd_preprocess(args)
    cases_path = args.output_dir / "cases.jsonl"
    prepare_case_workspaces(cases_path, args.output_dir)
    write_d3_rubric_inputs(cases_path, args.output_dir / "d3_rubric_inputs.json")
    sessions_dir = Path("sessions")
    if sessions_dir.exists():
        parse_sessions_dir(sessions_dir, args.output_dir / "parsed_session_trajectories.jsonl")
    responses_path = Path("openclaw_vllm_responses.jsonl")
    if responses_path.exists():
        parse_vllm_responses(responses_path, args.output_dir / "parsed_vllm_calls.jsonl")
    evaluate_cases(cases_path, args.output_dir)
    print(f"run-all complete under {args.output_dir}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EL Agent evaluation pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preprocess = subparsers.add_parser("preprocess", help="Build case specs, attachment manifest, and quality report")
    _add_common_paths(preprocess)
    preprocess.set_defaults(func=cmd_preprocess)

    workspaces = subparsers.add_parser("prepare-workspaces", help="Create per-case workspaces and copy attachments")
    workspaces.add_argument("--cases", type=Path, default=DEFAULT_OUTPUT / "cases.jsonl")
    workspaces.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    workspaces.set_defaults(func=cmd_prepare_workspaces)

    sessions = subparsers.add_parser("parse-sessions", help="Parse OpenClaw session jsonl files")
    sessions.add_argument("--sessions-dir", type=Path, default=Path("sessions"))
    sessions.add_argument("--output", type=Path, default=DEFAULT_OUTPUT / "parsed_session_trajectories.jsonl")
    sessions.set_defaults(func=cmd_parse_sessions)

    vllm = subparsers.add_parser("parse-vllm", help="Parse vLLM SSE response jsonl")
    vllm.add_argument("--responses", type=Path, default=Path("openclaw_vllm_responses.jsonl"))
    vllm.add_argument("--output", type=Path, default=DEFAULT_OUTPUT / "parsed_vllm_calls.jsonl")
    vllm.set_defaults(func=cmd_parse_vllm)

    evaluate = subparsers.add_parser("evaluate", help="Run implemented evaluators")
    evaluate.add_argument("--cases", type=Path, default=DEFAULT_OUTPUT / "cases.jsonl")
    evaluate.add_argument("--trajectories", type=Path, default=None)
    evaluate.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    evaluate.add_argument("--d3-rubrics", type=Path, default=None, help="fixed D3 rubrics JSON/JSONL generated once and reviewed")
    evaluate.add_argument("--d3-judge-base-url", default=None, help="OpenAI-compatible judge base URL; can also use D3_JUDGE_BASE_URL")
    evaluate.add_argument("--d3-judge-api-key", default=None, help="judge API key; can also use D3_JUDGE_API_KEY")
    evaluate.add_argument("--d3-judge-model", default=None, help="judge model; can also use D3_JUDGE_MODEL")
    evaluate.add_argument("--d3-pass-threshold", type=float, default=0.75)
    evaluate.set_defaults(func=cmd_evaluate)

    d3_inputs = subparsers.add_parser("prepare-d3-rubric-inputs", help="Build one-time rubric synthesis input for D3 candidate cases")
    d3_inputs.add_argument("--cases", type=Path, default=DEFAULT_OUTPUT / "cases.jsonl")
    d3_inputs.add_argument("--output", type=Path, default=DEFAULT_OUTPUT / "d3_rubric_inputs.json")
    d3_inputs.set_defaults(func=cmd_prepare_d3_rubric_inputs)

    d3_summary = subparsers.add_parser("summarize-d3-rubrics", help="Validate and summarize fixed D3 rubrics")
    d3_summary.add_argument("--d3-rubrics", type=Path, required=True)
    d3_summary.add_argument("--output", type=Path, default=DEFAULT_OUTPUT / "d3_rubric_summary.json")
    d3_summary.set_defaults(func=cmd_summarize_d3_rubrics)

    bundle = subparsers.add_parser("prepare-execution-bundle", help="Build portable execution bundle without copying attachment inputs")
    bundle.add_argument("--excel", type=Path, default=DEFAULT_EXCEL)
    bundle.add_argument("--attachments", type=Path, default=DEFAULT_ATTACHMENTS)
    bundle.add_argument("--bundle-dir", type=Path, default=DEFAULT_BUNDLE)
    bundle.add_argument("--skills-workbook", type=Path, default=Path("EL agent 专业技能清单.xlsx"))
    bundle.add_argument("--sessions-json", type=Path, default=Path("sessions/sessions.json"))
    bundle.set_defaults(func=cmd_prepare_execution_bundle)

    agent_bundle = subparsers.add_parser("prepare-agent-execution-bundle", help="Build a GT-free bundle for the real Agent execution machine")
    agent_bundle.add_argument("--source-bundle-dir", type=Path, default=DEFAULT_BUNDLE)
    agent_bundle.add_argument("--output-dir", type=Path, default=DEFAULT_AGENT_BUNDLE)
    agent_bundle.add_argument("--no-code", action="store_true", help="Do not copy src/, pyproject.toml, or scripts/openclaw_runner.py")
    agent_bundle.set_defaults(func=cmd_prepare_agent_execution_bundle)

    run_agent = subparsers.add_parser("run-agent", help="Run prepared cases through an external EL Agent/OpenClaw command")
    run_agent.add_argument("--manifest", type=Path, default=DEFAULT_BUNDLE / "run_manifest.json")
    run_agent.add_argument("--output", type=Path, default=DEFAULT_OUTPUT / "trajectories.jsonl")
    run_agent.add_argument("--command", required=True, help="shell command template; receives EL_EVAL_* env vars and {workspace}/{prompt_path}/{case_spec}/{agent_output}")
    run_agent.add_argument("--case-id", action="append", default=[], help="run only this case_id; can be repeated")
    run_agent.add_argument("--stop-on-error", action="store_true")
    run_agent.add_argument("--timeout-seconds", type=int, default=None)
    run_agent.set_defaults(func=cmd_run_agent)

    run_all = subparsers.add_parser("run-all", help="Run preprocessing, workspace prep, parser samples, and evaluation blockers")
    _add_common_paths(run_all)
    run_all.set_defaults(func=cmd_run_all)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
