# EL Agent 自动评测报告

生成日期：2026-06-17  
评测对象：EL Agent 筛选后测试集  
测试集规模：35 条 case  
本轮可用执行轨迹：33 条 trajectory  
缺失执行轨迹：`EL260529F-0001`、`EL260529F-0018`

## 1. 总览

### 1.1 报告大纲

本报告按“先看整体，再看每个维度”的方式组织：

1. 评测维度总览：说明本轮自动评测覆盖了哪些结果维度、哪些过程维度。
2. 评测结果汇总：用统一表格展示每个维度的可评分数量、平均分和状态分布。
3. Agent 能力短板：基于当前自动评测结果，概括当前 Agent 的主要问题。
4. 分维度细节：对 D1、D2、D3、D4、D5、D8 分别说明“是什么、怎么评、评估结果”。
5. 后续建议：列出需要优先补强的工程与标注事项。

### 1.2 本轮评测范围

本轮正式评测基于筛选后测试集，共 35 条 case。由于真实 Agent 执行目前只拿到了 33 条有效 trajectory，因此 `EL260529F-0001` 和 `EL260529F-0018` 在依赖 trajectory 的维度上会进入 `blocked`。

本轮自动评测实际覆盖 6 个维度：

| 类型 | 维度 | 名称 | 简要说明 |
|---|---:|---|---|
| 结果类 | D1 | 终态校验 | 检查 Agent 是否生成了预期文件、工具产物或状态结果。 |
| 结果类 | D2 | 答案匹配 | 对有明确标准答案的题目做规则匹配，如数值、文本片段、关键词集合。 |
| 结果类 | D3 | 答案质量 | 对开放式答案按专家 rubrics 逐条 LLM-Judge，并汇总加权分。 |
| 结果类 | D8 | Grounding 校验 | 检查最终回答中的关键 claim 是否能在工具结果证据中找到。 |
| 过程类 | D4 | 调用链匹配 | 检查 trajectory 中是否出现专家标注的 skills 和 tools。 |
| 过程类 | D5 | Tool Call 格式合法性 | 检查已记录 tool call 的名称、参数、JSON 格式是否可解析。 |

契约中的 D6、D7 本轮暂未纳入自动评测。D6 需要负样本或调用决策标注子集，D7 需要工具结果语义理解标注子集及 Judge 校准；当前 pipeline 先聚焦 D1、D2、D3、D4、D5、D8。

### 1.3 分数口径

`scored_count` 表示该维度实际产生数值分数的 case 数量。`average` 只对这些已评分 case 求均值，不包含 `blocked` 和 `not_applicable`。

`blocked` 表示当前证据不足，无法判断；例如缺少 trajectory、缺少 final response、缺少 tool evidence。`not_applicable` 表示题目本身不适用该维度；例如没有 `target_state` 的题目不跑 D1，没有 `gold_chain` 的题目不跑 D4。

### 1.4 评测结果汇总

| 维度 | 类型 | scored_count | average | pass | fail | blocked | not_applicable |
|---|---|---:|---:|---:|---:|---:|---:|
| D1 终态校验 | 结果 | 13 | 0.288 | 2 | 11 | 1 | 21 |
| D2 答案匹配 | 结果 | 10 | 0.536 | 3 | 7 | 1 | 24 |
| D3 答案质量 | 结果 | 19 | 0.383 | 6 | 13 | 0 | 16 |
| D4 调用链匹配 | 过程 | 26 | 0.173 | 2 | 24 | 2 | 7 |
| D5 Tool Call 格式合法性 | 过程 | 33 | 1.000 | 33 | 0 | 2 | 0 |
| D8 Grounding 校验 | 结果 | 16 | 0.631 | 2 | 14 | 19 | 0 |

D4 进一步拆分为 skills 和 tools 两个子项，各占 0.5 权重：

| D4 子项 | scored_count | average | min | max |
|---|---:|---:|---:|---:|
| skills | 26 | 0.269 | 0.000 | 1.000 |
| tools | 20 | 0.100 | 0.000 | 1.000 |

### 1.5 当前 Agent 能力短板

从当前 33 条可用 trajectory 看，Agent 的基础 tool call 格式非常稳定，D5 达到 1.000，说明被记录下来的 tool call 基本都能被解析，schema 层面没有明显问题。

但过程和结果之间存在明显断层：D4 平均分只有 0.173，尤其 tools 子项平均分只有 0.100。这说明从 trajectory 可见证据看，Agent 很少完整覆盖专家标注的技能和工具调用链。这里可能同时包含两类问题：一是 Agent 真实执行时没有调用预期 skill/tool；二是 trajectory 没有完整记录 skills 和 tools 的证据。当前自动评测只能基于可见证据给分。

D1 终态校验也偏弱，13 条可评分 case 中只有 2 条 pass，平均分 0.288。即使评测逻辑已经允许从 `tool_calls`、`tool_results`、`steps`、`final_response` 中模糊匹配相关文件，多数文件产物仍未能完整命中。这说明文件生成、输出路径、产物回收和 workspace 规范需要继续打通。

D2、D3 反映答案质量不稳定。D2 平均分 0.536，说明明确答案类题目中仍有不少数值或关键词未命中；D3 平均分 0.383，且 19 条开放式可评分 case 中 13 条 fail，主要原因是核心 rubric 未满足或 grounding 预检失败。

D8 的平均分为 0.631，但 blocked 数量很高，共 19 条，其中多数是缺少 tool evidence。这说明最终答案与工具证据之间的可追溯性仍不足。D8 当前是粗粒度规则检查，偏保守；但它暴露的问题是真实存在的：没有足够证据时，自动评测无法确认 Agent 的回答是否有依据。

## 2. 分维度细节

### 2.1 D1 终态校验

**是什么**

D1 评估 Agent 最终是否“做成了事”。对于文件型或工具产物型任务，D1 不只看最终回答文本，而是检查预期文件、报告、曲线、仿真产物等是否存在。

**怎么评**

当前 D1 的输入包括 case 中的 `target_state.required_files` 和 trajectory 中的执行证据。评测逻辑按以下顺序查找预期产物：

1. 优先检查 `sandbox_final_files` 或 workspace 终态快照。
2. 如果真实 sandbox 路径不可用，则在 `steps`、`tool_calls`、`tool_results`、`final_response` 中做文件名或路径片段的模糊匹配。
3. 对每个 required file 计算是否命中，D1 score = 命中文件数 / required file 总数。
4. 全部命中为 `pass`，部分或全部缺失为 `fail`，缺少必要 trajectory 或 workspace 证据为 `blocked`。

**评估结果**

| 指标 | 数值 |
|---|---:|
| scored_count | 13 |
| average | 0.288 |
| pass | 2 |
| fail | 11 |
| blocked | 1 |
| not_applicable | 21 |

典型例子：

| case_id | 结果 | 分数 | 说明 |
|---|---|---:|---|
| `EL260529F-0005` | pass | 1.000 | Setfos 相关产物在工具执行证据中均可匹配，包括 `generated_par`、`reports`、`optical_results` 等。 |
| `EL260529F-0004` | fail | 0.750 | 部分 Setfos 产物命中，但仍缺少 `optical_results/*/opt2D.txt`。 |
| `EL260529F-0015` | fail | 0.500 | 命中 `汇总.xlsx`，但缺少预期的 `TE-SHB-96测试原始数据_IVL曲线.xlsx`。 |
| `EL260529F-0018` | blocked | - | 缺少 trajectory，无法判断终态。 |

D1 暴露出的主要问题是：Agent 可能运行了工具，但产物没有稳定写入或回收到评测可见位置；也可能只是最终回答中提到了产物，但实际文件证据不足。后续真实执行时需要统一 per-case workspace、产物输出目录和 trajectory 中的 sandbox snapshot 记录。

### 2.2 D2 答案匹配

**是什么**

D2 评估答案空间可枚举的题目。它适合标准答案明确的场景，例如 HOMO/LUMO/T1 数值、特定文本结论、必须出现的一组关键词等。

**怎么评**

当前 D2 使用规则断言：

1. 读取 case 中的 `expected_answer.assertions`。
2. 对 final response 逐条检查断言，支持数值近似匹配、文本包含、任一文本命中、全部文本命中。
3. D2 score = 通过断言数 / 断言总数。
4. 全部断言通过为 `pass`，部分或全部未通过为 `fail`。
5. 如果 case 标注需要 D2，但缺少 final response 或 expected answer，则进入 `blocked`。

**评估结果**

| 指标 | 数值 |
|---|---:|
| scored_count | 10 |
| average | 0.536 |
| pass | 3 |
| fail | 7 |
| blocked | 1 |
| not_applicable | 24 |

典型例子：

| case_id | 结果 | 分数 | 说明 |
|---|---|---:|---|
| `EL260529F-0005` | pass | 1.000 | 预期答案断言全部命中。 |
| `EL260529F-0016` | pass | 1.000 | 预期答案断言全部命中。 |
| `EL260529F-0002` | fail | 0.250 | HOMO、LUMO、T1 等关键数值未按 expected answer 命中。 |
| `EL260529F-0003` | fail | 0.750 | 大部分断言命中，但仍有部分标准答案缺失。 |

D2 的结果说明：明确答案题仍不够稳，尤其在数值提取、单位表达、最终回答是否完整列出关键字段上存在波动。对于这类题，后续可优先要求 Agent 在最终回答中输出结构化结果，降低自动评测和人类复核成本。

### 2.3 D3 答案质量

**是什么**

D3 评估开放式答案质量。它不要求 final response 与参考答案逐字匹配，而是用专家确认后的 rubrics 检查回答是否覆盖关键判断、机制解释、风险分析和建议。

**怎么评**

当前 D3 使用 `outputs/pipeline/fixed_d3_rubrics_expert.json` 中的专家版 rubrics，并通过 OpenAI-compatible Judge 接口调用 `Qwen3.5-27B`。

评测逻辑如下：

1. D2 不适用或被标记为 D3 candidate 的题目进入 D3。
2. 每条 rubric 有 category 和 weight。默认权重为 core 0.45、major 0.45、minor 0.10，同类内平均分配。
3. Judge 对每条 rubric 输出 `pass`、`partial`、`fail`，分别映射为 1.0、0.5、0.0。
4. 每题按 rubric 权重汇总为 D3 score。
5. 通过条件是 score >= 0.75，且不能有 core rubric 判为 fail。
6. 如果 D8 已经给出分数且 D8 score < 0.5，则 D3 直接判为 `grounding_fail_precheck`，score = 0.0。

**评估结果**

| 指标 | 数值 |
|---|---:|
| scored_count | 19 |
| average | 0.383 |
| pass | 6 |
| fail | 13 |
| blocked | 0 |
| not_applicable | 16 |

失败原因分布：

| reason | 数量 | 含义 |
|---|---:|---|
| `core_rubric_failed` | 10 | 至少一个核心 rubric 未满足，即使总分较高也判 fail。 |
| `grounding_fail_precheck` | 3 | D8 grounding 分数低于 0.5，D3 直接失败。 |
| `answer_quality_passed` | 6 | 通过 D3 质量评估。 |

典型例子：

| case_id | 结果 | 分数 | 说明 |
|---|---|---:|---|
| `EL260529F-0011` | pass | 0.750 | 达到通过阈值，核心 rubric 未失败。 |
| `EL260529F-0034` | pass | 0.950 | 多数核心和主要 rubric 通过，仅 minor rubric 部分满足。 |
| `EL260529F-0010` | fail | 0.850 | 总分高于 0.75，但有 core rubric 失败，因此最终 fail。 |
| `EL260529F-0029` | fail | 0.000 | D8 grounding 预检分数过低，触发 `grounding_fail_precheck`。 |

D3 暴露出的主要问题是：Agent 在开放式专业分析中可以写出较完整的回答，但经常遗漏核心判断点，或者无法保证答案有充分工具证据支撑。当前 rubrics 机制能较好地区分“写得长”和“真正满足关键专业判断”。

### 2.4 D4 调用链匹配

**是什么**

D4 评估 Agent 是否按专家标注的专业 skill/tool 路径完成任务。它关注过程，而不是最终答案是否正确。

**怎么评**

当前 D4 使用 case 中的 `gold_chain`。为了适配真实 trajectory 中 skill/tool 记录不总是标准化的问题，评测逻辑已经放宽为：

1. 将 gold chain 拆分为 skills 和 tools 两类。
2. 在 `steps` 和 `tool_calls` 的结构化名称中匹配。
3. 如果名称未直接命中，则在 `steps` 和 `tool_calls` 的完整 JSON 文本中做模糊匹配。
4. skills 和 tools 分别计算 score。
5. D4 总分 = skills score * 0.5 + tools score * 0.5；如果某个子项本身不适用，则只按适用子项归一化。
6. 任一 required skill/tool 缺失则 D4 status 为 `fail`。

**评估结果**

| 指标 | 数值 |
|---|---:|
| scored_count | 26 |
| average | 0.173 |
| pass | 2 |
| fail | 24 |
| blocked | 2 |
| not_applicable | 7 |

子项结果：

| 子项 | scored_count | average | 说明 |
|---|---:|---:|---|
| skills | 26 | 0.269 | 专家标注的 skill 在 trajectory 中被匹配到的比例较低。 |
| tools | 20 | 0.100 | 专家标注的底层工具命中率更低，是当前 D4 的主要短板。 |

典型例子：

| case_id | 结果 | 分数 | 说明 |
|---|---|---:|---|
| `EL260529F-0005` | pass | 1.000 | `setfos-user` 和 `setfos-kernel.exe` 都在执行证据中命中。 |
| `EL260529F-0014` | fail | 0.500 | `ijp-blue-oled-rca` skill 命中，但 `root_cause_scoring.py`、`contribution_analysis.py`、`evidence_weight_update.py` 三个工具缺失。 |
| `EL260529F-0015` | fail | 0.500 | `ivl-chart-generator` skill 命中，但 `ivl_pipeline.py` 未命中。 |
| `EL260529F-0002` | fail | 0.000 | 预期 skill 和 tool 均未在可见 trajectory 证据中命中。 |

D4 结果需要谨慎解读：它既可能反映 Agent 未调用预期 skill/tool，也可能反映 trajectory parser 没有完整捕获 skill/tool 证据。下一步应把 skill 选择、skill 文件读取、底层工具执行、工具参数、工具结果统一记录进标准 trajectory。

### 2.5 D5 Tool Call 格式合法性

**是什么**

D5 评估已记录 tool call 的格式是否合规。它是过程层的基础健康检查，只判断“调用记录是否可解析”，不判断“该不该调用”或“调用是否有用”。

**怎么评**

当前 D5 的规则如下：

1. 遍历 trajectory 中的 `tool_calls`。
2. 如果有 `raw` 字段，要求其能被 JSON parse。
3. 如果没有 `raw`，则要求存在结构化 `args`。
4. tool call name 不能为空。
5. 所有已记录 tool call 都合法则 pass。

需要注意：D5 不要求一定调用工具；如果某条 trajectory 没有 tool call，但也没有格式错误，D5 仍然可以 pass。工具是否被正确选择和调用由 D4 负责。

**评估结果**

| 指标 | 数值 |
|---|---:|
| scored_count | 33 |
| average | 1.000 |
| pass | 33 |
| fail | 0 |
| blocked | 2 |
| not_applicable | 0 |

D5 是当前表现最好的维度。它说明真实执行环境中输出到 trajectory 的 tool call 记录格式基本稳定。后续优化重点不在 JSON schema 本身，而在工具调用链是否完整、工具产物是否可见、工具结果是否被最终答案正确使用。

### 2.6 D8 Grounding 校验

**是什么**

D8 评估最终回答是否有工具证据支撑。它用于识别“最终答案说了很多，但 trajectory 证据中找不到依据”的问题。

**怎么评**

当前 D8 是规则型粗检：

1. 从 final response 中抽取可检查 claim，主要包括数字、百分比、英文 token、文件名或参数片段。
2. 拼接 trajectory 中所有 `tool_results.content` 作为 evidence。
3. 检查每个 claim 是否能在 evidence 中精确出现。
4. D8 score = 命中 claim 数 / claim 总数。
5. score = 1.0 才判 pass；低于 1.0 判 fail。
6. 如果缺少 trajectory、final response 或 tool evidence，则进入 `blocked`。

**评估结果**

| 指标 | 数值 |
|---|---:|
| scored_count | 16 |
| average | 0.631 |
| pass | 2 |
| fail | 14 |
| blocked | 19 |
| not_applicable | 0 |

blocked 原因主要是缺少 tool evidence：19 条 blocked 中，17 条是 `blocked_by_missing_tool_evidence`，2 条是缺少 trajectory。

典型例子：

| case_id | 结果 | 分数 | 说明 |
|---|---|---:|---|
| `EL260529F-0014` | pass | 1.000 | final response 中抽取的 claim 全部能在 tool evidence 中匹配。 |
| `EL260529F-0034` | pass | 1.000 | 所有可抽取 claim 均有证据命中。 |
| `EL260529F-0005` | fail | 0.686 | 70 个 claim 中命中 48 个，部分如 `EQE`、`CIE`、`Peak`、`0.3266` 等未在 evidence 中精确匹配。 |
| `EL260529F-0002` | blocked | - | 缺少 tool evidence，无法进行 grounding 检查。 |

D8 当前偏严格，可能因为格式差异、同义表达、单位转换导致 false negative。但它对自动评测很有价值：凡是 D8 blocked 或低分的 case，都说明 trajectory 证据链不完整，或者最终答案没有把工具结果以可追溯方式引用出来。

## 3. 结论与建议

### 3.1 结论

当前 EL Agent 在“能否输出格式合法的 tool call”上表现很好，但在“是否正确选择并记录专业调用链”“是否稳定生成并回收文件产物”“最终答案是否覆盖专家核心判断并有证据支撑”上仍有明显短板。

如果用一句话概括：当前问题不在最底层 tool call JSON 格式，而在真实专业任务闭环。这个闭环包括 skill/tool 选择、工具执行、文件产物落地、trajectory 证据记录、最终答案引用证据。

### 3.2 建议优先级

1. 优先补齐真实执行 trajectory 证据：确保 skill 读取、tool 调用、tool result、sandbox/workspace 输出文件都进入标准 trajectory。
2. 统一文件产物输出约定：所有文件型 case 都应写入 per-case workspace，并在 trajectory 中记录最终文件列表、sha256、相对路径。
3. 对 D4 低分 case 做抽样人工复核：区分是 Agent 没调用预期 skill/tool，还是 parser/logging 没捕获。
4. 对 D1 fail 的文件型 case 做工具产物回收专项排查，尤其是 Setfos、IVL、LT、RDKit 相关 case。
5. 对 D2 题目要求 Agent 输出结构化答案，降低数值或关键词遗漏。
6. 继续使用专家 rubrics 校准 D3，并对 core rubric failed 的 case 做 targeted prompt/skill 改进。
7. 后续引入 D6、D7 前，先准备负样本和工具结果语义理解标注子集。

## 4. 输入与输出文件

本报告基于以下 pipeline 产物生成：

| 文件 | 说明 |
|---|---|
| `outputs/pipeline/cases.jsonl` | 筛选后 35 条标准 case。 |
| `outputs/pipeline/trajectories_available_no0001.jsonl` | 当前可用 33 条 trajectory。 |
| `outputs/pipeline/fixed_d3_rubrics_expert.json` | 专家修订后的 D3 rubrics。 |
| `outputs/pipeline/evaluation_results.jsonl` | case 级自动评测结果。 |
| `outputs/pipeline/evaluation_summary.json` | 汇总级自动评测结果。 |

