# 第二次评估打包说明

本文档记录“第二次评估”所需的代码与数据边界。第二次评估的核心变化是：对专家标注中需要使用 skill 的 case，在 `user_query` 前添加统一提示：

```text
请使用 技能 skill 完成任务:
```

该提示不暴露具体 skill 名称，只提醒 Agent 进入技能调用路径。

## 本次包内应包含的内容

- Pipeline 代码：`src/el_eval_pipeline/`
- OpenClaw runner 示例：`scripts/openclaw_runner.py`
- 项目依赖配置：`pyproject.toml`
- 正式筛选后测试集：`EL Agent测试集_260529_筛选后.xlsx`
- 专业 skill 清单：`EL agent 专业技能清单.xlsx`
- 测试附件源目录：`测试集相关文件/`
- D2 补充产物：`D2 缺答案补充数据/`
- 专家复核后的 D3 rubrics：`rubrics/fixed_d3_rubrics_expert.json`
- portable execution bundle：`execution_bundle/`

## 第二次 prompt 覆盖情况

当前 `execution_bundle/` 中：

- case 总数：35
- `requires_skill=true` 的 case 数：28
- 已添加 `请使用 技能 skill 完成任务:` 前缀的 skill case 数：28
- 非 skill case 数：7，不添加该前缀

## 不应提交到 GitHub 的内容

以下内容属于运行产物、历史日志或真实 Agent 输出，不作为本次 GitHub 包的一部分：

- `outputs/`
- `agent_execution_bundle/`
- `sessions/`
- `openclaw_vllm_request.json`
- `openclaw_vllm_responses.jsonl`
- `~$*` Office 临时锁文件
- `.DS_Store`

其中 `agent_execution_bundle/` 是从 `execution_bundle/` 生成的 GT-free 执行包，可在需要交给开发同事真实执行时本地生成；它本身不应进入 GitHub commit。

## 重新生成 portable bundle

```bash
PYTHONPATH=src python3 -m el_eval_pipeline.cli prepare-execution-bundle \
  --excel 'EL Agent测试集_260529_筛选后.xlsx' \
  --attachments '测试集相关文件' \
  --bundle-dir execution_bundle \
  --skills-workbook 'EL agent 专业技能清单.xlsx'
```

## 生成 GT-free Agent 执行包

该命令会生成 `agent_execution_bundle/`，并做泄露扫描。输出包只包含 Agent 执行所需的 prompt、脱敏 case spec、附件和 runner 代码，不包含 `reference_answer`、`expected_answer`、`gold_chain`、`target_state` 等评测答案字段。

```bash
PYTHONPATH=src python3 -m el_eval_pipeline.cli prepare-agent-execution-bundle \
  --source-bundle-dir execution_bundle \
  --output-dir agent_execution_bundle
```

预期输出：

```text
prepared GT-free agent execution bundle under agent_execution_bundle; cases=35 attachments=9 leak_scan=passed
```

## 上传前建议检查

```bash
git status --short

python3 - <<'PY'
import json

n = skill = prefixed = 0
missing = []
for line in open("execution_bundle/cases.jsonl", encoding="utf-8"):
    row = json.loads(line)
    n += 1
    if row.get("requires_skill"):
        skill += 1
        if row.get("user_query", "").startswith("请使用 技能 skill 完成任务:"):
            prefixed += 1
        else:
            missing.append(row["case_id"])

print({
    "cases": n,
    "requires_skill": skill,
    "prefixed_skill_cases": prefixed,
    "missing_prefix": missing,
})
PY
```

预期检查结果：

```text
{'cases': 35, 'requires_skill': 28, 'prefixed_skill_cases': 28, 'missing_prefix': []}
```
