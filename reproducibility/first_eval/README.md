# 第一次评估复现包

本目录保存第一次正式评估的最小复现输入与期望输出，用于在其他机器上检查评估 pipeline 逻辑是否一致。

## 背景

第一次评估使用的是原始 prompt，不包含第二次评估新增的：

```text
请使用 技能 skill 完成任务:
```

因此复现第一次评估时，必须使用本目录下的 `inputs/cases.jsonl`，不要使用当前 `execution_bundle/cases.jsonl`。后者是第二次评估 prompt 版本。

## 文件说明

输入文件：

- `inputs/cases.jsonl`：第一次评估使用的 CaseSpec，共 35 条。
- `inputs/trajectories_available_no0001.jsonl`：第一次评估可用 trajectories，共 33 条，缺 `EL260529F-0001` 和 `EL260529F-0018`。
- `inputs/fixed_d3_rubrics_expert.json`：专家复核后的 D3 rubrics。

期望输出：

- `expected_outputs/evaluation_results.jsonl`
- `expected_outputs/evaluation_summary.json`
- `expected_outputs/EL_Agent_自动评测报告.md`

## Judge 配置

第一次评估使用 OpenAI-compatible LLM Judge：

```text
model: Qwen3.5-27B
base_url: http://47.86.216.146:8850/v1
api_key: EMPTY
```

如果该 API 不可用，则无法完整复现 D3 相关 LLM Judge 结果，只能检查非 LLM 维度和已保存的期望输出。

## 复现命令

在 repo 根目录执行：

```bash
PYTHONPATH=src python3 -m el_eval_pipeline.cli evaluate \
  --cases reproducibility/first_eval/inputs/cases.jsonl \
  --trajectories reproducibility/first_eval/inputs/trajectories_available_no0001.jsonl \
  --output-dir outputs/repro_first_eval \
  --d3-rubrics reproducibility/first_eval/inputs/fixed_d3_rubrics_expert.json \
  --d3-judge-base-url http://47.86.216.146:8850/v1 \
  --d3-judge-api-key EMPTY \
  --d3-judge-model Qwen3.5-27B \
  --d3-judge-timeout-seconds 600 \
  --d3-judge-max-tokens 256
```

输出文件：

```text
outputs/repro_first_eval/evaluation_results.jsonl
outputs/repro_first_eval/evaluation_summary.json
```

## 对比摘要

```bash
python3 - <<'PY'
import json
from pathlib import Path

expected = json.load(open("reproducibility/first_eval/expected_outputs/evaluation_summary.json", encoding="utf-8"))
actual = json.load(open("outputs/repro_first_eval/evaluation_summary.json", encoding="utf-8"))

for dim in sorted(expected["dimensions"]):
    e = expected["dimensions"][dim]
    a = actual["dimensions"][dim]
    print(dim)
    print("  expected:", e.get("statuses"), e.get("scores"))
    print("  actual:  ", a.get("statuses"), a.get("scores"))
PY
```

说明：

- D1/D2/D4/D5/D8 中的规则型统计应高度一致。
- D3 依赖 LLM Judge，若模型服务、版本或输出格式变化，逐 rubric 判断可能有轻微差异。
- 若 `Qwen3.5-27B` API 不通，应先停止复现，不要把 D3 差异误判为 pipeline 逻辑问题。
