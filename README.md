# EL Agent Execution Bundle

This branch is for the real Agent execution machine only. It is intentionally
GT-free and should be cloned on the machine that has OpenClaw plus the real
business tools installed.

Do not merge evaluator-side files into this branch, including
`execution_bundle/cases.jsonl`, original annotation workbooks,
`d3_rubric_inputs.json`, `reference_answer`, `expected_answer`, `gold_chain`,
or `target_state`.

## Contents

- `run_manifest.json`: case execution manifest.
- `workspaces/<case_id>/prompt.txt`: user prompt for each case.
- `workspaces/<case_id>/agent_case_spec.json`: sanitized execution spec.
- `attachments/`: attachment files needed by cases.
- `scripts/openclaw_runner.py`: OpenClaw runner that calls `openclaw agent` and writes `agent_output.json`.
- `src/`: runner code that executes cases and writes `trajectories.jsonl`.

## Preconditions

- OpenClaw is installed and can run from the terminal.
- OpenClaw is configured for the target model, agent, and business tools.
- Python 3.10+ is available.
- The checkout is from the `agent-execution` branch, not `main`.
- This machine should not contain GT/evaluation files from the evaluator branch.

Quick checks:

```bash
git branch --show-current
openclaw --version
find . \( -name cases.jsonl -o -name case_spec.json -o -name d3_rubric_inputs.json \) -print
cat leak_scan_report.json
```

The branch should be `agent-execution`. The `find` command should print
nothing. `leak_scan_report.json` should show `"status": "passed"`.

On Windows PowerShell, use this equivalent file check:

```powershell
Get-ChildItem -Recurse -File -Include cases.jsonl,case_spec.json,d3_rubric_inputs.json
Get-Content .\leak_scan_report.json
```

## Setup

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

## Smoke Test

Run one no-attachment case first.

Linux/macOS:

```bash
REPO="$(pwd)"
.venv/bin/python -m el_eval_pipeline.cli run-agent \
  --manifest run_manifest.json \
  --output outputs/pipeline/openclaw_smoke_trajectories.jsonl \
  --case-id EL260529F-0029 \
  --stop-on-error \
  --timeout-seconds 900 \
  --command "$REPO/.venv/bin/python $REPO/scripts/openclaw_runner.py --session-id el-eval-EL260529F-0029-smoke"
```

Windows PowerShell:

```powershell
$Repo = (Get-Location).Path
$RunnerCommand = "`"$Repo\.venv\Scripts\python.exe`" `"$Repo\scripts\openclaw_runner.py`" --session-id el-eval-EL260529F-0029-smoke"
.\.venv\Scripts\python.exe -m el_eval_pipeline.cli run-agent `
  --manifest run_manifest.json `
  --output outputs/pipeline/openclaw_smoke_trajectories.jsonl `
  --case-id EL260529F-0029 `
  --stop-on-error `
  --timeout-seconds 900 `
  --command $RunnerCommand
```

Expected result:

- `outputs/pipeline/openclaw_smoke_trajectories.summary.json` has `"executed": 1`.
- `outputs/pipeline/openclaw_smoke_trajectories.jsonl` has one row with non-empty `final_response`.
- The final response should not be an upstream error such as `HTTP 502`.

Then run one attachment case to verify attachment materialization and tool trace parsing.

Linux/macOS:

```bash
REPO="$(pwd)"
.venv/bin/python -m el_eval_pipeline.cli run-agent \
  --manifest run_manifest.json \
  --output outputs/pipeline/openclaw_attachment_smoke_trajectories.jsonl \
  --case-id EL260529F-0001 \
  --stop-on-error \
  --timeout-seconds 900 \
  --command "$REPO/.venv/bin/python $REPO/scripts/openclaw_runner.py --session-id el-eval-EL260529F-0001-smoke"
```

Windows PowerShell:

```powershell
$Repo = (Get-Location).Path
$RunnerCommand = "`"$Repo\.venv\Scripts\python.exe`" `"$Repo\scripts\openclaw_runner.py`" --session-id el-eval-EL260529F-0001-smoke"
.\.venv\Scripts\python.exe -m el_eval_pipeline.cli run-agent `
  --manifest run_manifest.json `
  --output outputs/pipeline/openclaw_attachment_smoke_trajectories.jsonl `
  --case-id EL260529F-0001 `
  --stop-on-error `
  --timeout-seconds 900 `
  --command $RunnerCommand
```

Expected result:

- `outputs/pipeline/openclaw_attachment_smoke_trajectories.summary.json` has `"executed": 1`.
- The trajectory row contains `attachments`, `tool_calls`, `tool_results`, `model_calls`, and `final_response`.
- The attachment row should have `materialized=true` and matching `sandbox_sha256`.

## Run All Cases

Use a clean OpenClaw session namespace for each run. The command template supports
`{case_id}` and `{run_id}` placeholders.

Linux/macOS:

```bash
REPO="$(pwd)"
.venv/bin/python -m el_eval_pipeline.cli run-agent \
  --manifest run_manifest.json \
  --output outputs/pipeline/trajectories.jsonl \
  --timeout-seconds 900 \
  --command "$REPO/.venv/bin/python $REPO/scripts/openclaw_runner.py --session-id el-eval-{case_id}-{run_id}"
```

Windows PowerShell:

```powershell
$Repo = (Get-Location).Path
$RunnerCommand = "`"$Repo\.venv\Scripts\python.exe`" `"$Repo\scripts\openclaw_runner.py`" --session-id el-eval-{case_id}-{run_id}"
.\.venv\Scripts\python.exe -m el_eval_pipeline.cli run-agent `
  --manifest run_manifest.json `
  --output outputs/pipeline/trajectories.jsonl `
  --timeout-seconds 900 `
  --command $RunnerCommand
```

Use `--case-id <case_id>` to run a subset. The option can be repeated.

## OpenClaw Session Path

The runner reads OpenClaw session JSONL files to populate `tool_calls`,
`tool_results`, `steps`, and `model_calls`.

By default it reads:

- Linux/macOS: `~/.openclaw/agents/main/sessions`
- Windows: the equivalent OpenClaw home sessions directory

If OpenClaw stores session JSONL somewhere else, append `--sessions-dir <path>`
inside the command passed to `--command`.

Linux/macOS example:

```bash
REPO="$(pwd)"
.venv/bin/python -m el_eval_pipeline.cli run-agent \
  --manifest run_manifest.json \
  --output outputs/pipeline/trajectories.jsonl \
  --timeout-seconds 900 \
  --command "$REPO/.venv/bin/python $REPO/scripts/openclaw_runner.py --session-id el-eval-{case_id}-{run_id} --sessions-dir /path/to/openclaw/sessions"
```

## Output

Return these files to the evaluator:

- `outputs/pipeline/trajectories.jsonl`
- `outputs/pipeline/trajectories.summary.json`

Each trajectory row includes:

- `case_id`, `run_id`, `user_query`
- `final_response`
- `tool_calls`, `tool_results`, `steps`, `model_calls`
- `attachments`
- `sandbox_initial_files`, `sandbox_final_files`
- `runner.returncode`, `runner.stdout_tail`, `runner.stderr_tail`

Do not send full OpenClaw state unless debugging is required.

## Notes

- Running cases creates local artifacts under `.venv/`, `outputs/`, and
  `workspaces/<case_id>/`. They are execution artifacts and do not need to be
  committed.
- OpenClaw may print tool preflight logs before or after its JSON response. The
  runner is compatible with this noisy output and still extracts the JSON
  response plus session trace.
- A trajectory with `runner.returncode=0` and a non-empty `final_response` means
  the command ran. For evaluation quality, also check that the final response is
  not an upstream service error such as `HTTP 502`.
