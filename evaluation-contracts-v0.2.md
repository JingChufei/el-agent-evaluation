# Agent 评测维度判定契约集 · v0.2

> **本文档是当前业务下 8 个评测维度的完整判定契约集合,按 `evaluation-contract-template.md` 的 12 项模板填写。**
>
> **配套文档**: `evaluation-contract-template.md`(模板规范层)
> **版本**: v0.2 / 2026-05-29
> **状态**: 在 v0.1 基础上,将 D1 终态校验主路径从 mock 改为 sandbox 真实执行(详见附录 D 修订记录)

---

## 一、维度总览

| ID | 中文名 | 层级 | 过程/结果 | 主路径 | 需 per-case 标注 |
|---|---|---|---|---|---|
| D1 | 终态校验 | 结果层 | 结果式 | 规则(sandbox 快照 diff) | 是 |
| D2 | 答案匹配 | 结果层 | 结果式 | 规则 | 是 |
| D3 | 答案质量 | 结果层 | 结果式 | LLM-Judge | 主观参考答案 + 二分类 rubric |
| D4 | 调用链匹配 | 过程层·宏观 | 过程式 | 规则 | 是 |
| D5 | Tool Call 格式合法性 | 过程层·微观 | 过程式 | 规则 | 否 |
| D6 | 调用决策合理性 | 过程层·微观 | 过程式 | 规则 + 轻 Judge | 是(负样本子集) |
| D7 | Tool Result 语义理解 | 过程层·微观 | 过程式 | 规则 + Judge 兜底 | 子集人工标注 |
| D8 | Grounding 校验 | 质量层 | 结果式 | 规则 + NLI 兜底 | 否 |

---

## 二、执行顺序与依赖

| 顺序 | 维度 | 依赖 | 说明 |
|---|---|---|---|
| 1 | D5 调用格式 | 无 | 最先跑,零 ground-truth,发现基础语法问题 |
| 2 | D1 终态 | sandbox 就绪 | 结果层主判,能写 target_state 的 case 优先 |
| 3 | D4 调用链 | 配合 D1 | 终态通过但调用链不符 → 合理替代路径 |
| 4 | D6 调用决策 | 需负样本子集 | 单独跑,不和 normal case 混算 |
| 5 | D7 Result 语义 | 依赖 schema 中 is_empty/status | 仅在标注子集上跑 |
| 6 | D8 Grounding | 依赖 trajectory 证据完整 | 与 D7 互补 |
| 7 | D2 答案匹配 | 适用 case 才跑 | 答案空间可枚举的子集 |
| 8 | D3 答案质量 | D8 先过滤 | grounding 严重失败则 D3 不通过 |

---

## 三、维度间的关键联动

- **D1 + D4 联动判定"合理替代路径"**:D4 调用链不符但 D1 终态通过 → 标"合理替代",不算失败
- **D6 normal case 反向检查**:防止"什么都不调用"的退化策略
- **D4 误检率对照**:防止"反复瞎试也算恢复"
- **D8 失败联动 D3**:grounding 命中率 < 0.5 的回复在 D3 答案质量上直接判为不通过
- **D7 与 D8 互补**:D7 抓"中途歪了"(对 result 的当下解读),D8 抓"最后编了"(最终回复对全 trajectory 证据的使用)

---

# 维度契约正文

## D1 · 终态校验

**① 维度 ID**: D1_FINAL_STATE
**中文名**: 终态校验
**英文名**: Final State Verification

**② 评测对象**: Agent 在 sandbox 中真实执行工具后,环境关键状态变更是否符合预期。评测 Agent "**实际做成了什么**",而非"想做什么"。

**③ 所属层级**: 结果层
**过程/结果定位**: 结果式

**④ 评测输入**:
- Sandbox **初态快照**(pre-execution baseline,用于 diff 对照)
- Sandbox **终态快照**(post-execution snapshot):
  - 文件系统状态(指定路径下文件存在性、内容 hash、关键字段)
  - 数据库状态(指定表的关键字段值或 diff)
  - 工具产物状态(Setfos 输出文件、日志文件、配置文件等)
- `trajectory.tool_calls[]`(作为辅助证据,不作为主判定锚点)
- 极端不可逆操作的 mock 拦截记录(仅适用于兜底场景的少数工具)

**⑤ Ground-truth 需求**: 需要 per-case 标注

```yaml
target_state:
  # 文件系统终态
  required_files:
    - path: /sandbox/output/setfos_result.csv
      must_exist: true
      must_non_empty: true
      must_contain_keys: [efficiency, voltage, current]
      content_check:                # 可选: 内容层断言
        - field: efficiency
          range: [0.0, 1.0]         # 物理合理性,非答案校验
  
  # 数据库终态
  required_db_states:
    - table: experiments
      where: {experiment_id: "EXP_${case_id}"}
      assertions:
        - field: status
          equals: "completed"
        - field: result_path
          not_null: true
  
  # 工具内部产物
  required_tool_artifacts:
    - tool: setfos_4.6
      output_files: ["*.log", "*.dat"]
      log_assertions:
        - must_contain: "Convergence reached"
        - must_not_contain: ["FATAL", "Segfault"]
  
  forbidden_states:
    - file_deleted: "/sandbox/critical/*"
    - db_table_dropped: any
    - mock_intercepted_unauthorized: any   # 极端不可逆操作被 mock 拦截
```

**⑥ 输出指标**:
- case 级: 终态通过(0/1), 一票否决触发(0/1), 必要项命中率(0-1)
- 聚合级: 终态通过率, 一票否决触发率
- 细分: 按意图类别、按 case_type 的细分通过率
- Sandbox 健康度: 可复现率(同 case 重跑终态一致比例)

**⑦ 实现方法**:
- 主路径: 规则(sandbox 初态/终态快照 diff + assertion 校验)
- 兜底: 极端不可逆操作保留 mock 层,拦截记录作为 forbidden_state 辅助校验

**⑧ 判定规则**:
```
# Sandbox 准备阶段
sandbox = create_sandbox(case_id)
initial_snapshot = sandbox.snapshot()
# 初态校验: 确认与基线一致(防止上一条 case 污染)
ASSERT initial_snapshot.hash == baseline_hash

# Agent 执行阶段
agent.run(query, sandbox=sandbox)
# 真实工具在 sandbox 内被真实调用,真实改变 sandbox 状态
# 极端不可逆操作(数据库 drop、外部消息发送等)仍由 mock 层拦截
# 拦截事件写入 sandbox 日志,后续用于 forbidden_state 校验

# 状态采集阶段
final_snapshot = sandbox.snapshot()
diff = compute_diff(initial_snapshot, final_snapshot)

# 一票否决
IF any(forbidden_state matched in diff OR mock_intercept_log 
       for forbidden_state in target_state.forbidden_states):
    RETURN score=0, reason="forbidden_state_triggered"

# 必要项检查
hits = 0
total = total_assertions_count(target_state)

FOR each req in target_state.required_files:
    IF file_assertions_pass(final_snapshot, req):
        hits += 1

FOR each req in target_state.required_db_states:
    IF db_assertions_pass(final_snapshot, req):
        hits += 1

FOR each req in target_state.required_tool_artifacts:
    IF artifact_assertions_pass(final_snapshot, req):
        hits += 1

IF hits == total:
    RETURN pass=1, score=1.0
ELSE:
    RETURN pass=0, score=hits/total, missing_list=[...]

# Sandbox 清理阶段
sandbox.reset()  # 快照恢复或 destroy + recreate
```

**⑨ 边界条件与例外**:
- Sandbox 必须保证 case 间隔离(每条 case 跑完重置到干净初态)
- 工具版本必须固化在 sandbox 配置中,变更需 bump 契约版本并跑回归
- `target_state` 无法定义的开放式 case 不走此维度,降级到 D3 答案质量
- 与 D4 联动:终态通过但调用链不符 → 由 D4 标"合理替代路径"
- **极端不可逆操作保留 mock 兜底**:数据库 drop、文件系统格式化、外部消息发送、付款类操作
  - 此类操作在 sandbox 中由 mock 拦截,记录调用意图作为 forbidden_state 校验
  - mock 兜底工具清单需单独维护并版本化,作为契约附件
- 长耗时工具(如 Setfos 仿真 >2 分钟)启用结果缓存:同参数同版本 → 复用上次 sandbox 终态
- 评测并行化时必须保证 sandbox 实例间无共享资源(独立目录、独立数据库实例、独立工具进程)
- 外部依赖(只读 API、第三方查询)按需选择直连或 stub,在 sandbox 配置中显式声明

**⑩ 校准要求**:
- 规则维度,无 Judge κ 需求
- 人工抽检 30-50 例,验证 `target_state` 标注与业务认可的成功标准一致性 ≥ 0.95
- **Sandbox 可复现性验证**: 同一 case 重复跑 3 次,终态 hash 一致率 ≥ 0.95
  - 不达标 → 排查非确定性来源(时间戳、随机种子、并行干扰、工具版本漂移)
- 工具版本变更时必须重新跑回归基线
- Mock 兜底工具清单每季度回顾,确保不被遗漏或扩大
- 抽检发现的标注歧义回炉 M2 标注规范

**⑪ 失败模式与防 hacking**:
- "创建空文件骗过检查" → `must_non_empty` + `must_contain_keys` 防御
- "Agent 创建文件但内容是垃圾" → `required_files` 必须配 `content_check` 或 schema 校验
- "工具看似成功但实际没收敛" → `required_tool_artifacts` 校验工具日志(如 Setfos 的 "Convergence reached"),这是 sandbox 路线相对 mock 路线的核心增益
- "Sandbox 状态污染下一条 case" → 每 case 重置 + 抽样验证初态一致性
- "工具版本漂移导致跑分变化" → 工具版本写入契约,变更触发回归
- "并行评测时 sandbox 互相干扰" → 实例隔离 + 资源独占
- "Sandbox 隔离失效导致污染生产" → 网络/文件系统/数据库三层隔离 + 监控告警
- "极端不可逆操作 sandbox 也兜不住" → 该类操作保留 mock 兜底,断言调用意图,不真实执行
- "用其他工具达成等价终态" → 配合 D4 的合理替代路径判定,不在此维度处理
- "评测中工具实际失败但 Agent 编造成功" → sandbox 终态会暴露真实失败,这是 sandbox 路线相对 mock 的另一核心增益

**⑫ 版本**: v0.2 / 2026-05-29 / @owner

> v0.2: 主判定路径从 mock 调用意图核对改为 sandbox 真实执行 + 状态快照 diff。仅极端不可逆操作保留 mock 兜底。新增 sandbox 可复现性校准要求。

---

## D2 · 答案匹配

**① 维度 ID**: D2_ANSWER_MATCH
**中文名**: 答案匹配
**英文名**: Answer Matching

**② 评测对象**: 答案空间可枚举的 case 中,最终回复是否匹配标准答案。

**③ 所属层级**: 结果层
**过程/结果定位**: 结果式

**④ 评测输入**:
- trajectory 的最终回复文本 `trajectory.final_response`

**⑤ Ground-truth 需求**: 需要 per-case 标注

```yaml
expected_answer:
  type: exact | regex | numeric | set
  value: 0.85           # exact / regex / numeric
  tolerance:            # 仅 numeric
    abs: 0.01
    rel: 0.05
  acceptable_values:    # 仅 set
    - "选项A"
    - "选项 A"
```

**⑥ 输出指标**:
- case 级: 答案命中(0/1)
- 聚合级: 答案匹配率
- 细分: 按 `type` 细分的匹配率(exact / regex / numeric / set)

**⑦ 实现方法**:
- 主路径: 规则(归一化 + 比对)
- 兜底: 无

**⑧ 判定规则**:
```
SWITCH expected_answer.type:
  CASE exact:
    extracted = extract_answer(response)
    RETURN normalize(extracted) == normalize(expected_answer.value)

  CASE regex:
    RETURN re.search(expected_answer.value, response) is not None

  CASE numeric:
    extracted_num = extract_numeric(response)
    IF tolerance.abs:
      pass_abs = abs(extracted_num - expected_answer.value) <= tolerance.abs
    IF tolerance.rel:
      pass_rel = abs(extracted_num - expected_answer.value) / abs(expected_answer.value) <= tolerance.rel
    RETURN pass_abs OR pass_rel

  CASE set:
    extracted = extract_answer(response)
    RETURN normalize(extracted) in [normalize(v) for v in acceptable_values]
```

**⑨ 边界条件与例外**:
- 仅适用于答案空间可枚举的 case,开放式回复走 D3
- 数值容差必须业务方明确定义,不可由工程师拍脑袋
- 多答案可接受的 case 必须用 `set` 类型,不要用多个独立 case 拼凑
- `extract_answer` 必须精确抽取(从结构化字段或明确模板位置),不要全文 grep

**⑩ 校准要求**:
- 规则维度
- 人工抽检 30 例,验证 `expected_answer` 标注正确性 ≥ 0.98
- 容差设定需业务方签字,记录依据

**⑪ 失败模式与防 hacking**:
- "数值四舍五入差异被判错" → `normalize` 必须按业务定义的有效数字
- "答案包含正确值但夹杂其他内容" → `extract_answer` 精确抽取,不全文 grep
- "回复同时包含正确和错误答案" → extract 抽取规则需明确取哪个位置(首个/最后/标记位置)

**⑫ 版本**: v0.1 / 2026-05-29 / @owner

---

## D3 · 答案质量

**① 维度 ID**: D3_ANSWER_QUALITY
**中文名**: 答案质量
**英文名**: Answer Quality

**② 评测对象**: 开放式回复是否满足该 case 的主观答案质量要求。该维度判断最终回复相对专家/SOTA 主观参考答案和二分类 rubric 是否"通过/不通过"。

**③ 所属层级**: 结果层
**过程/结果定位**: 结果式

**④ 评测输入**:
- 用户原始 query `trajectory.user_query`
- trajectory 的最终回复 `trajectory.final_response`
- D8 grounding 命中率(用于预过滤)

**⑤ Ground-truth 需求**: 需要 per-case 主观参考答案 + 二分类 rubric

Ground-truth 构造流程:
1. 对测试题目,由专家手写一份标准主观答案;或由 SOTA 模型生成一份高质量主观答案,再由专家审核修订。
2. 基于标准主观答案,沉淀该 case 的答案质量 rubric:
   - 优先由专家手写 rubric 规则;
   - 可使用 LLM 从标准主观答案中抽取候选 rubric 规则,但必须经专家审核后进入测试集。
3. rubric 只服务于二分类判定:回复是否满足该 case 的关键质量要求,不做 1-5 分分档。

```yaml
subjective_reference_answer:
  source: expert | sota_model_then_expert_review
  answer: |
    专家认可的高质量主观答案。可包含必要解释、关键判断依据、
    边界条件、不确定性说明和不应过度断言的部分。

binary_rubric:
  generation_method: expert_written | llm_extracted_then_expert_review
  pass_if_all:
    - 覆盖用户问题中的核心诉求
    - 给出与参考答案一致的关键结论或合理等价结论
    - 覆盖参考答案中的必要依据/约束/前提
    - 对不确定或证据不足的部分有适当说明
  fail_if_any:
    - 回答主题明显偏离用户问题
    - 缺失关键结论或关键依据
    - 给出与参考答案冲突的核心判断
    - 编造 trajectory 或工具结果中不存在的事实
    - 在证据不足时做确定性断言
    - D8 grounding_rate < 0.5
  optional_quality_checks:
    - 表达清晰,没有明显歧义
    - 无大量无信息增益的冗余内容
```

**⑥ 输出指标**:
- case 级: 答案质量通过(0/1), fail_reason, Judge 理由, 命中的 pass/fail rubric 条目
- 聚合级: 答案质量通过率, 各 fail_reason 占比
- 细分: 按意图类别、难度、case_type 的通过率;与人工标注的 Judge κ

**⑦ 实现方法**:
- 主路径: LLM-Judge(二分类 rubric + 主观参考答案)
- 兜底: 人工抽检校准

**⑧ 判定规则**:
```
# Pre-filter: D8 联动
IF D8.grounding_rate < 0.5:
    RETURN pass=0, fail_reason="grounding_fail_precheck"

# Judge call
JUDGE_INPUT:
  query,
  response,
  subjective_reference_answer,
  binary_rubric.pass_if_all,
  binary_rubric.fail_if_any

JUDGE_PROMPT 要求:
  1. 先逐条检查 fail_if_any,任一命中则判 fail
  2. 再逐条检查 pass_if_all,所有必要项满足才判 pass
  3. 最终只输出 pass/fail,fail_reason,reasoning,matched_rules
  4. Judge 模型 ≠ 候选模型
  5. 若使用成对参考对比,启用位置轮换

POST-PROCESS:
  result = parse(judge_output)
  IF any(result.matched_fail_rules):
      RETURN pass=0, fail_reason=result.primary_fail_reason
  IF all_required_pass_rules_satisfied(result):
      RETURN pass=1
  ELSE:
      RETURN pass=0, fail_reason="missing_required_quality_points"
```

**⑨ 边界条件与例外**:
- D8 grounding 严重失败的回复在 D3 上直接不通过(预过滤逻辑)
- Judge κ 不达标时此维度保留人工,不允许无人值守跑
- 答案空间可枚举的 case 优先走 D2,不进 D3

**⑩ 校准要求**:
- κ 阈值 ≥ 0.6(标准开放式维度)
- 校准批次 100-200 条,分层覆盖意图类别与难度
- κ 不达标 → 二分类 rubric 条目改写或参考答案修订,重新校准
- 每月抽检 50 例监控 Judge 漂移

**⑪ 失败模式与防 hacking**:
- Judge 偏好"长且自信" → fail_if_any 中显式加入"无依据断言/冗余无信息增益"防御
- Judge 自我偏好 → 强制 Judge 模型 ≠ 候选模型
- Judge 位置偏置 → 成对比较位置轮换
- 模型"先编结论再加 hedging" → 通过 D8 grounding 联动直接判 fail

**⑫ 版本**: v0.2 / 2026-06-03 / @owner

---

## D4 · 调用链匹配

**① 维度 ID**: D4_TRAJECTORY_MATCH
**中文名**: 调用链匹配
**英文名**: Trajectory Matching

**② 评测对象**: Agent 实际调用序列与 gold chain 的吻合程度,同时覆盖过程正确性、错误恢复、合理替代路径。

**③ 所属层级**: 过程层·宏观
**过程/结果定位**: 过程式

**④ 评测输入**:
- `trajectory.steps[]`,每步含 `(name, type=skill|tool, args, status)`
- D1 终态结果(用于联动判定合理替代路径)

**⑤ Ground-truth 需求**: 需要 per-case 标注;采用"分段有序 + 局部并行集"表示

```yaml
gold_chain:
  stages:
    - type: ordered
      steps: [skill_route, skill_A.setup]
    - type: unordered
      steps: [tool_X, tool_Y]
    - type: ordered
      steps: [skill_A.aggregate, skill_A.report]
  forbidden:
    - "skill_X"
    - "tool_dangerous_*"
  allow_extra: true
  recovery_window: 3
```

**⑥ 输出指标**:
- case 级: 过程正确(0/1), 是否偏离(0/1), 是否回归(0/1), 是否触发 forbidden(0/1)
- 聚合级:
  - 过程正确率
  - 偏离发生率
  - **偏离回归率**(= 错误恢复率,核心诊断指标)
  - 平均偏离长度
  - **误检率**(无错误样本上的瞎重试比例,反向对照)
  - forbidden 触发率

**⑦ 实现方法**:
- 主路径: 规则(stage 顺序扫描 + 偏离/回归判定)
- 兜底: 无

**⑧ 判定规则**:

```
# 4a · 过程正确性
FOR each stage in gold_chain.stages (按序):
    IF stage.type == "ordered":
        检查 trajectory 中 stage.steps 按序出现
    ELIF stage.type == "unordered":
        检查 trajectory 中 stage.steps 集合被覆盖(顺序不限)

IF 所有 stage 满足 AND 未触发 forbidden:
    process_correct = 1
ELSE:
    process_correct = 0

# 4b · 错误恢复(基于偏离/回归)
按 gold_chain.stages 逐步对照 trajectory:
    若 step ∈ 当前可接受集合 → 正常推进
    若 step ∉ 当前可接受集合 → 进入"偏离区间"
    连续若干步回到可接受集合 → 标记为回归

分类:
    完全回归 + 后续走完剩余 gold chain → 成功恢复
    未回归但 forbidden 未触发 + D1 终态通过 → 合理替代路径
    未回归 + D1 终态未通过 → 恢复失败

# 4c · 误检率对照
对无偏离的 normal case 集合:
    统计其中出现"策略变更"(参数变化、换工具)的比例 → 误检率
```

**⑨ 边界条件与例外**:
- 终态(D1)通过但调用链不符 → 标"合理替代路径",不算失败
- forbidden 触发 → 一票否决,不论后续是否完成
- `recovery_window` 建议 3-5 步,过长会把"碰巧蒙对"也算回归
- 长 trajectory 的 stage 跳跃可能误判偏离 → 必要时手动放宽 N 步
- 此维度仅评过程结构,不评结果(D1)、不评单次调用合法性(D5)

**⑩ 校准要求**:
- 规则维度
- 人工抽检 50 例,验证 `gold_chain` 标注与"业务认可的正确路径"一致性 ≥ 0.9
- 抽检的 case 应分层覆盖所有意图类别
- 不达标 → 回炉 M2 标注规范,补充 stage 拆分指引

**⑪ 失败模式与防 hacking**:
- "反复瞎试也最终回归"被误判为恢复 → 误检率作对照指标
- "合理替代路径"被误判为失败 → 必须与 D1 终态结果联动判定
- gold_chain 标注过于刚性,等价路径被误判 → `allow_extra` 和 `unordered` stage 提供缓冲
- 模型"提前调用"被误判偏离 → 设计 `stages` 时把灵活步骤放进 `unordered`

**⑫ 版本**: v0.1 / 2026-05-29 / @owner

---

## D5 · Tool Call 格式合法性

**① 维度 ID**: D5_TOOL_CALL_SCHEMA
**中文名**: Tool Call 格式合法性
**英文名**: Tool Call Schema Compliance

**② 评测对象**: 每次工具调用的语法和 schema 合法性——函数名存在、参数名匹配、类型正确、必填齐全、取值在允许范围。

**③ 所属层级**: 过程层·微观
**过程/结果定位**: 过程式

**④ 评测输入**:
- `trajectory.tool_calls[]`(raw,未经过预解析)

**⑤ Ground-truth 需求**: 不需要 per-case 标注;依赖工具 schema 注册表

```yaml
tool_registry:
  setfos_simulate:
    params:
      wavelength:
        type: int
        required: true
        range: [400, 800]
      mode:
        type: str
        required: false
        enum: [fast, accurate]
```

**⑥ 输出指标**:
- 调用级: 是否合法(0/1), 错误类型
- case 级: 是否出现任何格式错误(0/1), 该 case 的格式错误次数
- 聚合级:
  - 合法调用率
  - 按错误类型细分计数: `unknown_tool` / `schema_mismatch` / `missing_required` / `value_out_of_range` / `json_parse_error`

**⑦ 实现方法**:
- 主路径: 纯规则(AST / schema 校验)
- 兜底: 无

**⑧ 判定规则**:
```
FOR each tool_call in trajectory:
    # JSON 解析
    IF parse(tool_call.raw) fails:
        error = "json_parse_error"; continue

    # 函数名
    IF tool_call.name not in tool_registry:
        error = "unknown_tool"; continue

    schema = tool_registry[tool_call.name]

    # 必填检查
    IF any(p.required and p.name not in tool_call.args for p in schema.params):
        error = "missing_required"; continue

    # 类型 / 范围检查
    FOR each (k, v) in tool_call.args:
        IF k not in schema.params:
            error = "schema_mismatch"; break
        IF type(v) != schema.params[k].type:
            error = "schema_mismatch"; break
        IF schema.params[k].enum and v not in enum:
            error = "value_out_of_range"; break
        IF schema.params[k].range and v out of range:
            error = "value_out_of_range"; break

    IF no error: tool_call.legal = True
```

**⑨ 边界条件与例外**:
- 此维度只看语法/schema,"该不该调"归 D6
- "格式合法但语义错误"不在此维度处理
- 工具 schema 注册表必须版本化,工具升级时同步更新
- schema 注册表本身的正确性是此维度的前提

**⑩ 校准要求**:
- 规则维度
- 人工抽检 50 例,验证错误类型标注精确率 ≥ 0.95
- 工具新增或 schema 变更时需重新抽检

**⑪ 失败模式与防 hacking**:
- "格式合法但不该调用" → 该问题归 D6,不在 D5
- "JSON 形式正确但 schema 缺失字段" → schema 注册表必须完整,定期校验
- "用近似函数名混过去"(如 `Setfos_Simulate` vs `setfos_simulate`) → 严格大小写匹配

**⑫ 版本**: v0.1 / 2026-05-29 / @owner

---

## D6 · 调用决策合理性

**① 维度 ID**: D6_CALL_DECISION
**中文名**: 调用决策合理性
**英文名**: Call Decision Reasonableness

**② 评测对象**: Agent 是否在不该调用时避免调用、信息不足时反问、能力外时拒绝;同时检测过度保守。

**③ 所属层级**: 过程层·微观
**过程/结果定位**: 过程式

**④ 评测输入**:
- `trajectory.steps[]`
- 用户原始 query
- case_type 标注

**⑤ Ground-truth 需求**: 需要 per-case 标注(专门负样本子集)

```yaml
case_type: insufficient_info | out_of_scope | no_tool_needed | normal
expected_behavior:
  should_call_tool: false
  should_ask_clarification: true   # 仅 insufficient_info
  should_refuse: true              # 仅 out_of_scope
clarification_keywords:            # 业务化定制
  - "请问"
  - "能否提供"
  - "哪一个"
```

**⑥ 输出指标**:
- case 级: 通过(0/1),按 case_type 分别报
- 聚合级(必须分类型报,不混算总分):
  - `insufficient_info` 通过率
  - `out_of_scope` 通过率
  - `no_tool_needed` 通过率
  - **`normal` 过度保守率**(反向指标)

**⑦ 实现方法**:
- 主路径: 规则(关键词 + 调用检测)
- 兜底: 轻 Judge(规则无法确定时)

**⑧ 判定规则**:
```
SWITCH case_type:
  CASE insufficient_info:
    IF no critical_tool_call AND response contains clarification_keywords:
        pass = 1
    ELIF no critical_tool_call AND no clarification_keywords:
        # 规则无法确定是否反问 → Judge 兜底
        pass = judge_judges_clarification(query, response)
    ELSE:
        pass = 0

  CASE out_of_scope:
    IF no business_tool_call AND response 明确说明能力边界:
        pass = 1
    ELSE:
        pass = 0

  CASE no_tool_needed:
    IF no tool_call AND response 直接回答:
        pass = 1
    ELSE:
        pass = 0

  CASE normal:
    IF triggered_any_refusal_behavior:  # 反向检查
        pass = 0
    ELSE:
        pass = 1
```

**⑨ 边界条件与例外**:
- 测试集必须主动构造负样本子集,不能只从真实 log 采样(真实 log 大多是 normal case)
- normal case 的反向检查是关键对照,不可省略
- "拒绝"和"过度保守"的边界靠 case_type 标注预先界定
- 各 case_type 通过率必须独立报告,不允许混算总分

**⑩ 校准要求**:
- 规则部分: 人工抽检 50 例,验证 case_type 标注准确率 ≥ 0.95
- Judge 兜底部分: κ ≥ 0.7(决策类维度,要求高于标准 0.6)
- 关键词词表需业务化定制,首版从 M2 标注阶段反向收集

**⑪ 失败模式与防 hacking**:
- "什么都不调用"的退化策略 → normal case 反向检查
- "假装反问其实在拖延" → clarification_keywords 需配合上下文 Judge 校验
- "过度拒绝" → 在 out_of_scope 之外的 normal case 不应触发拒绝行为
- 关键词词表过窄导致误判 → 词表迭代化,持续从 bad case 补充

**⑫ 版本**: v0.1 / 2026-05-29 / @owner

---

## D7 · Tool Result 语义理解

**① 维度 ID**: D7_RESULT_SEMANTIC
**中文名**: Tool Result 语义理解
**英文名**: Tool Result Semantic Understanding

**② 评测对象**: Agent 对工具返回结果的语义读取是否准确,特别是空 / 异常 / 低置信度结果。

**③ 所属层级**: 过程层·微观
**过程/结果定位**: 过程式

**④ 评测输入**(仅标注子集):
- `trajectory.tool_results[]` 的 raw content
- 紧接其后的 Agent 输出文本

**⑤ Ground-truth 需求**: **不在全集上跑**;需要人工标注子集(100-300 条)

```yaml
labeled_subset:
  - case_id: ...
    tool_name: setfos_simulate
    tool_result: |
      {"status": "ok", "data": null}
    actual_is_empty: true              # 业务语义上是否为空
    agent_reading: 成功 | 空 | 错误    # Agent 实际把它当作什么
    next_agent_action: |
      "实验完成,效率为 0.85"
```

**⑥ 输出指标**:
- 子集级: 语义读取错误率
- 细分:
  - 空结果误判率(`actual_is_empty=true` 中被读为"成功"的比例)
  - 异常结果误判率(`status=error` 中未触发恢复的比例)
- 按工具细分: 各工具下的误判率(诊断长尾工具)

**⑦ 实现方法**:
- 主路径: 规则(基于人工标注的 `actual_is_empty` / `agent_reading` 字段比对)
- 兜底: Judge 二元判定(标注存疑时)

**⑧ 判定规则**:
```
# 仅在标注子集上跑

FOR each labeled_case in subset:
    IF labeled_case.actual_is_empty == true:
        IF labeled_case.agent_reading == "成功":
            semantic_error = 1; type = "empty_misread"
        ELIF labeled_case.agent_reading == "空":
            semantic_error = 0
        ELSE:
            # 标注存疑 / Judge 兜底
            semantic_error = judge_binary(
                tool_result, next_agent_action,
                instruction="判断 Agent 的下一步输出是否与该 tool result 的实际语义一致"
            )

    ELIF labeled_case.tool_status == "error":
        IF agent triggered recovery_action in next N steps:
            semantic_error = 0
        ELSE:
            semantic_error = 1; type = "error_unrecognized"
```

**⑨ 边界条件与例外**:
- 不在全集上跑 → 报告中独立列示,标注子集规模透明
- 子集随业务工具变更需要补充标注,版本化管理
- 此维度依赖人工标注质量,**不可与规则维度直接合算总分**
- "穷举 `is_empty` 规则"的尝试已放弃,改为人工标注子集

**⑩ 校准要求**:
- 人工标注子集本身需要 ≥ 2 名标注者交叉验证,κ ≥ 0.8
- 子集规模 100-300 条,分层覆盖工具类型与"空"形态
- Judge 兜底部分: κ ≥ 0.7
- 子集每季度回顾,补充新工具与新发现的"空"形态

**⑪ 失败模式与防 hacking**:
- "穷举 is_empty 规则永远漏" → 已采用人工标注子集替代
- 子集污染(被模型见过) → 子集独立于训练数据,版本隔离
- 标注主观性 → 双盲交叉验证 + κ 检查
- "Agent 用模糊措辞掩盖误判" → next_agent_action 字段保留完整文本,Judge 二元判定时看上下文

**⑫ 版本**: v0.1 / 2026-05-29 / @owner

---

## D8 · Grounding 校验

**① 维度 ID**: D8_GROUNDING
**中文名**: Grounding 校验
**英文名**: Grounding Verification

**② 评测对象**: 最终回复中的事实性断言(数值、实体、状态判断)是否能在 trajectory 内找到证据支撑。

**③ 所属层级**: 质量层
**过程/结果定位**: 结果式

**④ 评测输入**:
- `trajectory.tool_results[]` 的全部内容
- 最终回复全文 `trajectory.final_response`
- 关键 tool 的 `is_empty` 字段(配合粒度 2)

**⑤ Ground-truth 需求**: **不需要 per-case 标注**;可选标注 `grounding_anchors`(关键值清单,用于精确校验)

```yaml
optional_annotation:
  grounding_anchors:
    - value: 0.85
      type: numeric
      source: "setfos result"
    - value: "样品 A-123"
      type: entity
```

**⑥ 输出指标**:
- 断言级: 命中(0/1), entailment 标签(粒度 3 适用)
- case 级:
  - grounding 命中率(0-1)
  - 强行下结论触发(0/1)
- 聚合级: 平均 grounding 命中率, 强行下结论触发率
- 明细: 未命中断言清单(进 bad case 库)

**⑦ 实现方法**:
- 主路径:
  - 粒度 1: 关键字匹配(全 case)
  - 粒度 2: 空结果断言检测(条件触发)
- 兜底:
  - 粒度 3: NLI Judge(抽样 5-10% + 人工质疑样本)

**⑧ 判定规则**:

```
# 粒度 1 · 关键字匹配(全 case)
key_values = extract_key_values(response)
  # 优先结构化提取(模板字段),正则兜底(数值/实体)

trajectory_evidence = concat([r.content for r in trajectory.tool_results])

hits = 0
unhit_list = []
FOR each kv in key_values:
    normalized_kv = normalize(kv)
      # 数值精度归一化(0.85 vs 0.850)
      # 百分号与小数互转
      # 单位归一化
      # 千分位/科学计数法转标准
    IF find_in_evidence(normalized_kv, trajectory_evidence):
        hits += 1
    ELSE:
        unhit_list.append(kv)

grounding_rate = hits / len(key_values) if key_values else None
IF grounding_rate is not None AND grounding_rate < 1:
    case_grounding_violation = 1


# 粒度 2 · 空结果断言检测(条件触发)
IF any(critical_tool.is_empty for critical_tool in trajectory):
    segments = split_response_by_paragraph(response)
    main_body = segments[:int(0.8 * len(segments))]

    has_certain_assertion_in_main = any(
        contains(seg, certainty_words) for seg in main_body
    )
    has_hedging_anywhere = any(
        contains(seg, hedging_words) for seg in segments
    )

    IF has_certain_assertion_in_main AND NOT has_hedging_anywhere:
        overclaim_triggered = 1


# 粒度 3 · NLI Judge 兜底(抽样 + 质疑样本)
IF case in (random_5_to_10_percent OR human_questioned_pool):
    claims = extract_atomic_claims(response)  # 轻量 LLM
    FOR each claim:
        evidence = retrieve_evidence(claim, trajectory)
          # embedding 召回 or 关键词召回
        label = judge_entailment(
            claim, evidence,
            constraint="仅基于给定 evidence,不依赖外部知识"
        )
        # 输出: entailed | contradicted | not_enough_info
```

**⑨ 边界条件与例外**:
- 数值断言必须先归一化再比对
- 不要全文 grep,要在原子值层匹配(避免 0.85 错匹到 "0.8500元/股")
- Hedging 检测必须分段判定,主体段落 vs 尾句(防"遮羞布")
- 粒度 3 不全量跑,仅 5-10% 抽样 + 人工质疑样本(成本控制)
- 文本性断言不适合粒度 1,直接走粒度 3
- "短回复无可抽取断言" → 边界处理,返回 N/A 而非满分

**⑩ 校准要求**:
- 粒度 1: 规则,人工抽检 50 例,精确率 ≥ 0.85
- 粒度 2: 规则,人工抽检 50 例,精确率 ≥ 0.8(hedging 词表是关键变量)
- 粒度 3: Judge κ ≥ 0.6(NLI 任务标准)
- Hedging / 确定性断言词表需业务化,从 M2 标注阶段反向收集

**⑪ 失败模式与防 hacking**:
- "0.85 vs 0.850" 假阳性 → normalize 必须先做
- "结尾加 hedging 当遮羞布" → 分段判定,主体段落是检测重点
- "短回复绕过检测" → 边界处理返回 N/A,不给满分
- "Judge 用外部知识脑补" → 粒度 3 Judge 输入必须封闭,instruction 显式约束
- "全文 grep 错匹" → 原子值层匹配,不做全文搜索
- 词表过窄导致漏判 → 词表持续迭代,从 bad case 库反向补充

**⑫ 版本**: v0.1 / 2026-05-29 / @owner

---

# 附录

## 附录 A · 共享词表(业务化定制起点)

以下为初版词表,需团队根据 EL Agent 实际话术持续迭代。所有词表应版本化管理,变更需记录在契约 ⑫ 中。

**A.1 Hedging 词表**(D8 粒度 2 使用):
```
未能获取 / 数据不足 / 无法判断 / 待进一步实验 / 结果未确认
暂无相关 / 信息有限 / 尚不明确 / 需补充验证 / 建议复核
```

**A.2 确定性断言词表**(D8 粒度 2 使用):
```
实验成功 / 结果显示 / 已确认 / 可以得出 / 证实
等于 / 为 / 是 / 表明 / 证明
```

**A.3 Clarification 词表**(D6 使用):
```
请问 / 能否提供 / 您是指 / 哪一个 / 请明确
是否需要 / 您希望 / 麻烦补充
```

## 附录 B · Trajectory Schema 字段需求清单

以下字段在 M0 阶段需要冻结到 trajectory schema 中。缺失字段会让对应维度退化或无法实现。

| 字段路径 | 类型 | 依赖维度 | 缺失影响 |
|---|---|---|---|
| `user_query` | str | D3, D6 | D3/D6 无法跑 |
| `tool_calls[].name` | str | D4, D5 | D4/D5 无法跑 |
| `tool_calls[].args` | dict | D5 | D5 无法跑 |
| `tool_calls[].raw` | str | D5 | json_parse_error 检测失败 |
| `tool_results[].content` | str/dict | D7, D8 | D7/D8 严重退化 |
| `tool_results[].status` | enum | D4 (4b), D7 | 错误恢复检测失败 |
| `tool_results[].is_empty` | bool | D7, D8 | D8 粒度 2 失效 |
| `final_response` | str | D2, D3, D8 | 结果层全部失效 |
| `steps[]`(含 skill/tool 顺序) | list | D4 | D4 无法跑 |
| `sandbox_initial_snapshot_ref` | str | D1 | D1 无法做 diff 对照 |
| `sandbox_final_snapshot_ref` | str | D1 | D1 无法采集终态 |
| `sandbox_intercept_log[]`(极端不可逆操作的 mock 拦截记录) | list | D1 | D1 forbidden_state 校验失效 |

## 附录 C · 维度依赖与互补关系图

```
执行顺序:
  D5 (无依赖) → D1 (sandbox 就绪) → D4 (配合 D1)
              ↓
  D6 (负样本子集) → D7 (标注子集) → D8 (依赖 trajectory)
              ↓
  D2 (适用 case) → D3 (D8 预过滤)

关键联动:
  D1 + D4  →  合理替代路径判定
  D8 → D3   →  grounding 严重失败则 D3 不通过
  D4 误检率  →  防"反复瞎试"
  D6 normal反向 →  防"什么都不调用"
  D7 + D8  →  互补(中途歪 vs 最后编)
```

## 附录 D · 修订记录

- **v0.1 / 2026-05-29**: 初稿,基于团队讨论形成 8 个维度判定契约。

- **v0.2 / 2026-05-29**: D1 终态校验主路径从 mock 调用意图核对改为 **sandbox 真实执行 + 状态快照 diff**。具体变更:
  - ② 评测对象内涵升级:从评测"想做什么"改为评测"实际做成了什么"
  - ④ 评测输入:新增 sandbox 初态/终态快照,trajectory 降为辅助证据
  - ⑤ Ground-truth schema 重构:从"调用意图标注"改为"终态特征标注",新增内容层断言、工具产物校验
  - ⑦ 实现方法:主路径改为 sandbox 快照 diff,极端不可逆操作保留 mock 兜底
  - ⑧ 判定规则:重写为"sandbox 准备 → 执行 → 采集 → 判定 → 清理"五阶段流程
  - ⑨ 边界条件:新增 sandbox 隔离、版本固化、长耗时缓存、并行隔离、不可逆操作 mock 兜底等约束
  - ⑩ 校准要求:新增 sandbox 可复现性验证(重复 3 次终态 hash 一致率 ≥ 0.95)
  - ⑪ 失败模式:新增 sandbox 特有失败模式(版本漂移、并行干扰、隔离失效),并明确 sandbox 路线相对 mock 的核心增益(暴露工具真实失败、发现编造成功)
  - 附录 B:trajectory schema 字段需求中,`mock_log` 改为 `sandbox_initial_snapshot_ref`、`sandbox_final_snapshot_ref`、`sandbox_intercept_log`(后者仅记录极端不可逆操作的 mock 拦截)

- **v0.2-D3 / 2026-06-03**: D3 答案质量从 1-5 分制改为二分类对/错判定。Ground-truth 改为"专家/SOTA 主观参考答案 + 二分类 rubric",rubric 可由专家手写或 LLM 抽取后专家审核。D8 grounding 严重失败时,D3 由"封顶 2 分"改为"直接不通过"。
