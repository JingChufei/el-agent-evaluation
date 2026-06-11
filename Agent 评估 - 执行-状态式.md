# Agent 评估 - 执行/状态式

> 从范式定义到工程落地, 兼顾研究与工程视角的系统梳理

---

## 一、是什么: 范式与核心思想

### 1.1 核心定义

**执行/状态式评估的核心, 是评估 Agent 执行后"世界状态是否正确", 而不是评估它中间说了什么、怎么想、走了哪条路径.**

它把 Agent 看成一个会改变环境的执行系统: 给定一个初始状态和任务目标, Agent 通过多轮交互、工具调用、文件操作、API 调用或环境操作产生变化, 最后由校验器检查最终状态是否满足目标. 

这里的"状态"指任务执行后可以被机器读取和校验的事实, 例如代码仓库里的补丁、单元测试结果、数据库中的订单状态、浏览器页面里的表单结果、操作系统里的文件、MCP 工具返回的数据、CRM 记录、支付状态、审计日志等. 

它和文本式评估的分界点很清楚: 文本式评估看"答案是否像正确答案", 执行/状态式评估看"系统是否真的被改对". 例如用户要求退款, 文本式评估可能检查 Agent 是否解释了退款流程; 执行/状态式评估检查订单状态、退款记录、支付状态、审计日志是否全部正确. 

### 1.2 基本链路

```text
任务目标
  → 初始状态 (initial_state)
  → Agent 执行 (tool calls / 多轮交互)
  → 环境状态变化
  → 终态 (final_state)
  → 校验器对比 target_state
  → 成功 / 失败 / 部分成功
```

整个链路有一个朴素但关键的设计哲学: **不关心路径, 只关心结果是否可机器校验**. 这让评估天然具备客观性、可重放性、可回归性. 

### 1.3 核心假设与边界

这类评估的核心假设是: **只要任务目标可以被状态化, 就可以通过终态检查判断任务是否完成.**

它天然适合: 

- 有状态、多轮、真实工具调用的 agentic 任务
- 真实环境操作 (代码、数据库、文件系统、GUI、MCP 工具生态)
- 目标可程序化校验、有客观对错的任务

它的边界也很清楚, 以下目标难以完全状态化, 需要其它范式补充: 

- **审美与创造性目标**: 设计是否好看、文案是否打动人
- **战略判断**: 一个商业决策是否正确
- **开放式写作**: 文章风格、论证质量
- **长期用户满意度**: 必须事后观测, 无法在沙盒内即时校验

### 1.4 评估范式光谱

把当前主流 Agent 评估范式放在一起对比, 各自的优劣矩阵如下: 

| 维度 | 文本式 (Text-based) | 过程式 (Process-based) | 执行/状态式 (State-based) |
|------|---------------------|------------------------|---------------------------|
| **看什么** | 最终回复文本 | 调用链 / 推理路径 | 环境终态 |
| **客观性** | 低 (依赖 LLM-as-judge 或人工) | 中 (依赖 gold trajectory) | 高 (机器可校验) |
| **诊断能力** | 弱 | 强 (能定位到具体步骤) | 中 (知道错了, 不一定知道错在哪一步) |
| **构造成本** | 低 (一对 Q/A 就行) | 高 (需要标注 gold 调用链) | 高 (需要沙盒 + 校验器) |
| **覆盖能力** | 广 (任何文本任务) | 中 (需要工具调用场景) | 中 (需要目标可状态化) |
| **抗"会说不会做"** | 差 | 中 | 强 |
| **与上线相关度** | 低 | 中 | 高 |

三种范式不是替代关系, 而是互补关系. 状态式评估的优势是客观、抗"会说不会做"、与上线相关度高; 弱点是诊断能力中等 (final_state 错时不一定知道错在哪一步) 、构造成本高. 这正是后面方法论章节需要解决的问题. 

### 1.5 最小示例 (贯穿全文)

为了让后续章节有一个共同锚点, 这里给出一个最小示例, 后面会反复引用. 

**用户输入**: 

```text
帮我删除最早的一条消息, 并给 Frank 发一条午餐邀请. 
```

**文本式评估**会看 Agent 是否回复了"已完成"之类的话. 

**状态式评估**则检查: 

```text
最早消息 (message_id=3) 是否被删除
新消息 (message_id=7) 是否出现在 inbox
新消息的 sender_id / receiver_id 是否正确
新消息内容是否包含午餐邀请语义
其它消息是否未被误改
wifi / logged_in 状态是否未被破坏
```

只要任一关键字段不满足, 任务就算失败  ——  无论 Agent 回复说得多漂亮. 

---

## 二、Benchmark 全景与演进脉络

**主线: 代码状态 → 业务数据库状态 → 工具状态 → 操作系统状态 → 工具生态状态**. 

这条演进线说明了一个趋势: **Agent 评估正在从"回答正确"转向"执行正确", 从静态题目转向动态环境, 从单工具调用转向真实多工具生态.**

下面每个 benchmark 不只讲"是什么", 还会讲它的**设计动机、核心选择、自身局限**, 这是研究向读者最关心的部分. 

### 2.1 SWE-bench: 代码仓库与单元测试

**场景**: Agent 根据真实 GitHub issue 修改代码, 最终通过单元测试判断补丁是否解决问题. 

**状态载体**: 代码仓库的文件状态 + 单元测试的运行结果. 

**设计动机**: 之前的代码评估 (如 HumanEval) 任务太短, 一个函数级别的题目无法反映真实开发场景. SWE-bench 用真实 GitHub issue 把任务推到 repo 级别. 

**核心设计选择**: 坚持用**单元测试**而不是 LLM-as-judge 做评分. 单元测试是高度客观的终态校验, 代价是构造数据集时必须人工挑选有完备测试的 issue. 

**自身局限**: 

- 强烈依赖 issue 自带的测试质量, 测试覆盖不到的逻辑无法校验
- 真实 issue 分布偏向 Python 开源仓库, 泛化性受限
- 无法评估"代码风格、可维护性"这类非功能性指标

### 2.2 τ-bench: 业务数据库终态

**场景**: Agent 在模拟客服、订单、航空等业务环境中多轮对话并调用 API, 最后检查数据库终态是否符合预期. 

**状态载体**: 业务数据库 (订单表、用户表、航班表). 

**设计动机**: SWE-bench 的状态过于"工程化", τ-bench 想测的是"业务侧 Agent"  ——  Agent 需要遵守业务规则 (退改签政策、退款条件) 并与用户多轮澄清. 

**核心设计选择**: 

- 引入 **User Simulator** 与 Agent 多轮交互, 而不是单轮指令
- 校验时同时检查**数据库终态**和**对话满足度**, 后者用 LLM-as-judge
- 加入业务约束 (policy), Agent 违反约束就算失败, 即便最终订单状态"看起来对"

**自身局限**: 

- User Simulator 行为偏离真实用户, 评测结果有偏
- 业务规则人工编码, 扩展成本高
- LLM-as-judge 部分仍有主观性

### 2.3 ToolSandbox: 有状态工具调用

**场景**: 有状态工具之间的隐式依赖、多轮工具执行、用户模拟器和中间 milestone. 

**状态载体**: 工具内部状态 (例如"WiFi 是否开启"会影响"能否发消息"). 

**设计动机**: 此前的工具调用评估 (BFCL 等) 都是无状态的单次调用, 但真实 Agent 场景里, 工具之间存在大量隐式依赖  ——  不开 WiFi 就不能联网、不登录就不能下单. ToolSandbox 把这种隐式状态依赖显式化. 

**核心设计选择**: 引入 **milestone 校验**, 不只看最终状态, 还看中间是否经过关键节点. 这是状态式评估和过程式评估的一次融合尝试. 

**自身局限**: milestone 需要人工设计, 限制了任务可以有"多条正确路径"的灵活度. 

### 2.4 OSWorld: 真实桌面/GUI 状态

**场景**: Agent 在操作系统和 GUI 环境中完成文件、浏览器、应用软件等任务, 最后通过桌面状态、文件状态、应用状态判断是否成功. 

**状态载体**: 操作系统层面的真实状态 (文件、注册表、应用窗口、浏览器 DOM). 

**设计动机**: 工具调用是结构化的, 但真实人机交互大量发生在 GUI 上. OSWorld 把评估推进到真实操作系统层. 

**核心设计选择**: 用**脚本探测器**校验终态 (检查文件是否存在、内容是否正确、应用是否处于某状态). 这种校验需要为每个任务单独写探测脚本. 

**自身局限**: 

- 每个任务都需要专属探测脚本, 扩展成本极高
- 桌面状态空间巨大, 边界条件难穷举
- 环境差异 (操作系统版本、应用版本) 导致复现成本高

### 2.5 MCP-Universe: 真实工具生态

**场景**: Agent 在真实 MCP servers 构成的工具世界里完成跨领域、长链路、多工具、动态数据任务. 覆盖地图导航、代码仓库、金融分析、3D 设计、浏览器自动化、网页搜索等. 

**状态载体**: 跨多个真实 MCP server 的状态. 

**设计动机**: τ-bench 和 ToolSandbox 还是在"模拟"工具, MCP-Universe 直接接入**真实**的 MCP server 生态, 这更接近未来 Agent 的实际部署环境. 

**核心设计选择**: 三类校验器并存

- **格式校验** (输出结构是否合法)
- **静态答案校验** (确定性答案直接比对)
- **动态 ground truth 校验** (实时查询真实数据源, 例如股价、地图)

**自身局限**: 真实工具的可用性、变化、限流影响评测稳定性; 评测结果不易完全复现. 

### 2.6 ACEBench: 抽象沙盒 + E2E Accuracy

**场景**: 通过多智能体交互模拟真实多轮工具使用场景, 覆盖手机应用、外卖、金融、旅行等抽象场景. 

**状态载体**: class / instance attributes 表示的抽象环境对象. 

**设计动机**: 真实环境复现成本高, ACEBench 选择构造**抽象但完整**的沙盒, 用 attribute-level 的 exact match 衡量 E2E Accuracy. 

**核心设计选择**: 

- 环境用结构化对象表示, 工具调用直接修改对象属性
- E2E Accuracy 是**样本级二值指标**: 终态完全匹配为 1, 否则为 0
- 同时输出 Process Accuracy 作为辅助诊断

**自身局限**: 

- 抽象环境与真实业务有 gap
- exact match 过严, 对"功能等价但表述不同"的结果不友好

### 2.7 横向对比

#### 场景维度对比

| Benchmark | 状态载体 | 工具真实度 | 长程性 | 与上线相关度 |
|-----------|----------|------------|--------|--------------|
| SWE-bench | 代码 + 测试 | 真实 (git/pytest) | 中 | 高 (软件工程) |
| τ-bench | 业务数据库 | 模拟 API | 中 | 高 (客服/电商) |
| ToolSandbox | 工具内部状态 | 模拟工具 | 中 | 中 |
| OSWorld | 操作系统 | 真实 OS/GUI | 长 | 高 (桌面 Agent) |
| MCP-Universe | 真实 MCP server | 真实 | 长 | 高 (通用 Agent) |
| ACEBench | 抽象对象 | 模拟 | 中 | 中 |

#### 设计选择维度对比 (研究向更关心)

| Benchmark | 校验粒度 | 多正确路径 | 需要人工 gold | 用 LLM-as-judge |
|-----------|----------|------------|---------------|-----------------|
| SWE-bench | 测试通过/失败 | 允许 | 否 (有测试) | 否 |
| τ-bench | 数据库字段 + 对话 | 部分允许 | 部分需要 | 部分用 |
| ToolSandbox | milestone + 终态 | 不太允许 | 需要 milestone | 否 |
| OSWorld | 探测脚本 | 允许 | 需要写脚本 | 否 |
| MCP-Universe | 格式/静态/动态 | 允许 | 部分需要 | 部分用 |
| ACEBench | attribute exact match | 不允许 (默认) | 需要 target_state | 否 |

### 2.8 演进趋势

把六个 benchmark 串起来, 可以看到几条清晰的演进线: 

第一, **状态载体从"工程化"转向"业务化", 再转向"真实生态化"**. 从代码 → 数据库 → 工具状态 → 操作系统 → 真实 MCP 生态, 评测的环境越来越接近真实部署. 

第二, **校验从"单一终态"转向"多类型校验组合"**. 早期只比对终态, 现在 MCP-Universe 把格式/静态/动态校验组合使用, ToolSandbox 把终态和 milestone 组合. 

第三, **评测对象从"单步工具调用"转向"长程多工具任务"**. 任务长度从 1-2 步推到几十步, 中间允许失败重试、工具错误恢复. 

第四, **评测开始关注"过程合规"而不只是"结果正确"**. τ-bench 的 policy 约束、ToolSandbox 的 milestone, 都是在试图把"过程对不对"纳入状态式评估框架. 

---

## 三、执行/状态式评估的方法论

**核心问题: 怎么把一句自然语言任务, 翻译成可校验的状态结构.**

这一章是整篇最有方法论密度的部分, 同时服务两类读者  ——  工程向读者会拿它当**任务设计 SOP**, 研究向读者会从 trade-off 讨论中找研究入口. 

### 3.1 任务结构: 三类状态

任何一个状态式评估任务都可以拆成三类状态: 

| 状态 | 含义 | 谁来定义 |
|------|------|----------|
| `initial_state` | 任务开始前的环境 | 任务设计者 (从真实数据冻结) |
| `final_state` | Agent 执行后的环境 | Agent 行为决定 |
| `target_state` | 期望达成的环境 | 任务设计者 (基于业务目标) |

校验逻辑是: 

```text
initial_state
  → Agent 执行
  → final_state
  → compare(final_state, target_state)
  → pass / fail
```

**关键设计原则**: `target_state` 应该尽量描述"任务目标的最小充分条件", 而不是"Agent 完成任务后环境的完整快照". 前者允许多条正确路径, 后者会把无关变化也卡死. 

### 3.2 断言体系

把校验拆成三类断言, 是这套方法论的核心. 

#### 目标断言 (Goal Assertions)

检查任务有没有完成. 例如退款任务: 

```text
Refund.status == "approved"
Order.status == "refunded"
User.balance 增加 59.99
```

#### 约束断言 (Constraint Assertions)

检查业务规则有没有被遵守. 例如: 

```text
退款金额 == 订单金额 (不能多退)
退款时间 <= 订单完成后 30 天
退款理由 in 合法理由列表
```

#### 副作用断言 (Side Effect Assertions)

检查是否误改了无关状态. 例如: 

```text
Inventory 不应改变
其它订单不应被修改
UserAccount 不应被删除
不应重复扣款
不应发送外部邮件
```

**为什么三类断言要分开**: 真实业务中最危险的不是 Agent 没完成任务, 而是它**完成了一个表面目标, 同时破坏了其他系统状态**. 把目标和副作用分开断言, 才能精准捕捉到"表面成功、实际有副作用"的危险情况. 

### 3.3 关键指标

#### E2E Accuracy (端到端准确率)

**Hard E2E**: 

```text
全部目标断言 + 约束断言 + 副作用断言都通过 → 1
任一关键断言不通过 → 0
```

**Soft E2E**: 

```text
matched_fields / total_fields
```

Hard E2E 用于**排行榜和上线门控**, soft E2E 用于**诊断分析**. 

#### Process Accuracy

看关键调用链是否走到. 是状态式评估的"过程视角"补充: 

```text
E2E Acc:    事情是否办成
Process Acc: 关键步骤走到哪里
```

E2E 正确时, 通常 Process Acc 也被认为满分. 但 E2E 错时, Process Acc 能告诉你"错在哪一步". 

#### False Success Rate (虚假成功率)

Agent 声称完成, 但 final_state 不满足 target_state 的比例. 这是**上线最敏感**的指标  ——  它直接对应"Agent 自信地告诉用户'好了', 但实际没做对". 

#### Side Effect Rate (副作用率)

Agent 完成目标的同时, 触发了禁止副作用的比例. 同样是**上线敏感指标**  ——  它对应"完成任务但顺便闯祸". 

#### 约束通过率 (Constraint Pass Rate)

约束断言的通过比例. 业务规则越严格, 这个指标越关键. 

#### 成本指标

- 工具调用步数
- token 消耗
- 端到端延迟
- 调用失败重试次数

### 3.4 关键 trade-off 讨论

这一节是研究向读者最该看的部分. 状态式评估里有几组核心张力, 任何一个落地方案都是在这些张力之间做选择. 

#### Trade-off 1: Hard vs Soft E2E

- **Hard E2E 的优点**: 区分能力强, 不容易被"表面接近"骗过, 适合排行榜
- **Hard E2E 的缺点**: 诊断能力差, 一个字段错和十个字段错都是 0
- **Soft E2E 的优点**: 诊断能力强, 能区分"接近完成"和"完全没做"
- **Soft E2E 的缺点**: 容易刷分  ——  Agent 可以通过"做对一半"获得不错的分数

**实践建议**: 两个都跑, 但用 Hard E2E 做最终判定, Soft E2E 做诊断辅助. 

#### Trade-off 2: Exact Match vs Constraint-based Match

- **Exact match**: 可重放性高, 实现简单, 但对"功能等价、表述不同"的结果不友好 (例如订单备注多了一个句号就算失败)
- **Constraint-based match**: 真实业务多样性更好 (例如允许"价格 ≤ 500、舱位为经济舱"这种约束), 但实现复杂, 不同任务的约束语言难统一

**实践建议**: 任务可控的字段用 exact match (例如订单状态、支付状态), 任务允许多样性的字段用 constraint match (例如航班选择、推荐结果). 

#### Trade-off 3: 完成度 vs 安全性

- **重完成度**: 强调目标断言, 容易得到"看起来能干活"的 Agent, 但可能引入副作用风险
- **重安全性**: 强调副作用断言和约束断言, 容易得到"不敢动"的 Agent, 完成率低

**实践建议**: 不同任务族用不同权重. 低风险任务 (打标签、写备注) 偏完成度; 高风险任务 (退款、改订单) 偏安全性, 副作用断言权重高于目标断言. 

#### Trade-off 4: 状态校验 vs 过程校验

- **纯状态校验**: 客观性强, 但盲区是"终态对、过程错". 例如 Agent 误删了一个订单, 又重新创建了一个看起来一样的订单, 终态匹配但过程违规
- **纯过程校验**: 过程合规, 但盲区是"过程对、终态错". 例如 Agent 调用了所有"应该调用"的工具, 但参数错了, final_state 仍然不对

**实践建议**: 以状态校验为主, 用 milestone 或 process accuracy 做补充. 高风险任务额外增加"过程不变量"断言 (例如"不允许删除现存订单"). 

### 3.5 匹配模式

| 模式 | 形式 | 适用场景 |
|------|------|----------|
| Exact match | `actual == expected` | 确定性任务 (订单状态、支付状态) |
| Constraint-based | `field in range / set / pattern` | 多正确答案 (航班选择、推荐) |
| Acceptable variants | `actual in [v1, v2, v3]` | 离散多答案 (推荐 3 个 SKU 之一) |
| Set match | 集合相等, 不计顺序 | 列表类字段 (商品集合) |
| Subset match | `expected ⊆ actual` | 至少满足某些条件 |
| Semantic match | LLM-as-judge 判断语义等价 | 自然语言字段 (订单备注、邮件正文) |

### 3.6 任务集设计与分层

#### 任务来源: 从真实业务蒸馏

不要"凭空想"任务. 真实业务任务应该从以下来源蒸馏: 

- 线上日志 (用户请求 + 实际处理结果)
- 客服工单
- CRM 操作记录
- 销售流程
- 财务审批
- 研发 issue
- 运维 ticket

蒸馏流程: **采样 → 脱敏 → 冻结状态 → 构造 sandbox 初始态 → 标注 target_state 和约束**. 

#### 评测集分层

| 层 | 目的 | 规模 | 使用频率 |
|----|------|------|----------|
| 冒烟集 (smoke) | 快速验证 prompt/tool/模型改动 | 5–20 个 | 每次提交 |
| 回归集 (regression) | 防止旧能力退化 | 100–500 个 | 每日/每周 |
| 压力集 (stress) | 长链路、多约束、工具错误 | 50–200 个 | 每周/每发布 |
| 红队集 (red team) | 越权、诱导、边界规则、高风险 | 30–100 个 | 每发布 |

### 3.7 失败归因体系

只输出 pass/fail 不够, 必须能定位失败原因. 推荐的归因分类: 

| 归因类型 | 含义 | 处理优先级 |
|----------|------|------------|
| `goal_not_completed` | 目标完全没完成 | 中 |
| `partial_completion` | 完成了一半 | 中 |
| `wrong_target` | 完成了错的目标 (例如改了别人的订单) | **高** |
| `missing_action` | 缺少关键动作 | 中 |
| `wrong_argument` | 参数错误 | 低 |
| `constraint_violation` | 违反业务规则 | **高** |
| `forbidden_side_effect` | 触发禁止副作用 | **高** |
| `tool_error_unrecovered` | 工具报错未恢复 | 低 |
| `false_success` | 系统未变但 Agent 声称完成 | **极高** |

特别需要重点跟踪 **`false_success_rate`** 和 **`forbidden_side_effect rate`**, 因为这两个指标直接关系到上线风险. 

### 3.8 完整 case 字段模板

一个完整的状态式评估 case 应该包含以下字段: 

```text
case_001/
  prompt.txt              # 用户输入
  initial_state.json      # 沙盒初始状态
  target_state.json       # 期望终态
  allowed_tools.json      # 允许使用的工具白名单
  constraints.json        # 约束断言 (业务规则)
  forbidden_side_effects.json  # 禁止副作用断言
  gold_milestones.json    # 关键过程节点 (可选)
  acceptable_variants.json     # 多正确答案 (可选)
  metadata.json           # 任务族、风险等级、来源
```

`metadata.json` 推荐字段: 

```json
{
  "task_family": "refund_processing",
  "risk_level": "high",
  "source": "production_log_2024Q3",
  "evaluator": "evaluators/refund_v2.ts",
  "expected_steps": 5
}
```

这套 schema 是工程与研究的共同语言: 工程侧拿来跑评测, 研究侧拿来做任务分布分析、能力诊断、模型对比. 

---

## 四、如何落地实现

**从通用工程组件到 OpenClaw 改造方案**. 

### 4.1 整体架构

一个完整的状态式评估系统由六层组件构成, 它们之间是单向的数据流, 各自承担清晰的职责. 

**最上游是 scenario / task spec 层**, 它是评测的输入. 一个 task spec 包含用户 prompt、initial_state、target_state、约束、禁止副作用、允许工具白名单等字段. 这一层是**声明式的**, 不包含任何执行逻辑, 只描述"这个任务是什么、什么样算成功". 这种声明式设计的好处是任务可以被独立审阅、版本化管理, 也方便研究侧做任务分布分析. 

**第二层是 sandbox / workspace**, 负责把 task spec 物化成一个独立可执行的环境. 每个 case 都有自己的独立目录、独立数据库 seed、独立 mock service 实例, 互不干扰. sandbox 的职责是把 initial_state 还原成 Agent 可操作的环境, 并在评测结束后导出 final_state. 它存在的根本理由是**可重放性**  ——  同一个 case 多次运行必须从相同初始态出发, 才能让评测结果可比较. 

**第三层是 Agent 本身**, 在 OpenClaw 这个上下文里就是推理内核. 这一层**不应该为了评估而被改动**, 因为评估的目的就是测真实 Agent 行为, 改动推理内核会让评测结果偏离线上语义. Agent 接收来自 sandbox 的环境信息和 prompt, 输出工具调用和回复, 整个过程对 evaluator 是黑盒. 

**第四层向下分叉成两条并行的支路**: 

一条是 **business tools**, 即 Agent 调用的业务工具. 关键约束是这些工具必须**真正修改 sandbox state**, 体现真实业务副作用 (改订单、扣款、发消息) , 而不是仅返回一段成功文本. 这是整套评估能够生效的根本前提  ——  如果工具不真改状态, 就只能评估"调用文本", 退化成过程式评估. 

另一条是 **plugin hooks**, 它是观测和评测触发器. 通过 `session_start` 绑定 task/run ID, `before_tool_call` / `after_tool_call` 记录完整工具轨迹, `agent_end` 触发 evaluator. 这一层的设计原则是**只观察、不干预**  ——  hooks 不修改 Agent 的推理路径, 也不修改工具的执行结果, 它们只负责记录和触发. 

**第五层是 final_state + trace.json**, 即评测的两份核心产出物. final_state 反映"做对了什么", trace 反映"怎么做的". 两者结合, 才能既支持状态校验, 也支持过程归因. 这两份文件应该是**纯数据、可序列化、可长期归档**的, 便于后续重新分析、对比不同版本 Agent 的行为. 

**最下游是 evaluator**, 它读取 final_state、target_state、trace、constraints, 输出结构化的评测结果, 包括 e2e_hard / e2e_soft / process_score / safety_pass / attribution 等字段. evaluator 是无状态的纯函数  ——  给定相同输入, 永远输出相同结果. 这让评测结果可被独立验证、可被回放重算, 也支持评测协议本身的版本化升级 (评测协议改了, 历史 trace 可以用新 evaluator 重新打分). 

整套架构的设计哲学可以归纳成三点: **task spec 声明式、Agent 不被改动、评测器无状态可重放**. 这三点共同保证了状态式评估的客观性、可复现性和长期可维护性. 

### 4.2 通用工程组件

#### Sandbox 隔离与可重放

每个 case 必须独立 workspace, 避免状态串扰: 

```text
/tmp/agent-eval/run-2024-09-15/case_001/
/tmp/agent-eval/run-2024-09-15/case_002/
```

每次评测前从 `initial_state.json` 恢复, 评测后 dump `final_state.json`. 同一个 case 多次运行应该得到可比较的结果. 

#### 状态存储方案选型

| 存储方式 | 适用场景 | 复杂度 |
|----------|----------|--------|
| `state.json` | 小型 benchmark, 抽象环境 | 极低 |
| SQLite | 订单/库存/用户/支付等结构化业务 | 低 |
| Redis | 会话状态、临时缓存 | 中 |
| 内存对象 | 单进程快速评估 | 极低 |
| Docker volume + 真实服务 | 接近生产的高保真评估 | 高 |

**选型原则**: 从 `state.json` 开始, 业务复杂度上来后再升级. 不要一开始就上 Docker. 

#### 工具 API 的副作用契约

工具必须**真的修改状态**, 否则只能评估函数调用的"文本", 无法评估 E2E 状态. 

每个业务工具应该: 

```text
1. 校验参数 (参数错则返回错误, 不修改 state)
2. 读取当前 state
3. 修改 state (体现业务语义)
4. 写回 state
5. 返回 observation (告知 Agent 修改结果)
```

#### Trace Logger

记录工具调用轨迹, 服务于失败归因: 

```json
[
  {
    "step": 1,
    "timestamp": "2024-09-15T10:00:00Z",
    "tool": "turn_on_wifi",
    "args": {},
    "observation": { "ok": true },
    "latency_ms": 12
  },
  {
    "step": 2,
    "tool": "delete_message",
    "args": { "message_id": 3 },
    "observation": { "ok": true, "deleted": true }
  }
]
```

#### Evaluator 输出结构

evaluator 不应只输出 pass/fail, 应该输出诊断友好的结构化结果: 

```json
{
  "case_id": "case_001",
  "e2e_hard": 0,
  "e2e_soft": 0.83,
  "process_score": 0.67,
  "constraint_pass": true,
  "safety_pass": false,
  "false_success": false,
  "state_diff": {
    "missing_fields": ["MessageApi.inbox.7.message"],
    "wrong_fields": [],
    "forbidden_changes": ["BaseApi.wifi changed from true to false"]
  },
  "attribution": "forbidden_side_effect",
  "cost": {
    "steps": 5,
    "tokens": 1240,
    "latency_ms": 8200
  }
}
```

### 4.3 在 OpenClaw 中落地

#### 改造原则

**不动 Agent 推理内核, 在外围构造评测闭环.**

不建议优先修改的地方: 

- `runEmbeddedAgent`
- `agentCommand`
- prompt assembly
- model resolve
- session queue
- tool calling core

**为什么不改这些**: 执行/状态式评估要测**真实 Agent 行为**, 如果为了评估而魔改推理内核, 评估结果会偏离线上真实运行语义, 失去评估的意义. 

#### 推荐改造点

**1. 扩展 `extensions/qa-lab`**

新增模块: 

```text
extensions/qa-lab/src/
  state-eval/
    runner.ts          # 执行 case 的主循环
    task-spec.ts       # 任务 schema 定义
    environment.ts     # setup / reset / teardown
    evaluator.ts       # evaluator interface
    assertions.ts      # 三类断言实现
    reporter.ts        # 结果聚合与报告生成
  scenario-packs.ts    # 注册 state-eval pack
```

**2. 扩展 `qa/scenarios`**

普通 scenario 只描述用户输入是不够的, 状态式 scenario 还要包含: 

```text
qa/scenarios/state-eval/
  refund_basic_001/
    scenario.yaml        # 注册任务到 qa-lab
    prompt.txt
    initial_state.json
    target_state.json
    allowed_tools.json
    constraints.json
    forbidden_side_effects.json
    evaluator.ts         # 任务特定校验逻辑 (可选)
    metadata.json
```

**3. 改造评测环境**

每个 case 独立 workspace, 独立数据库 seed, 独立 mock service. 避免污染真实 `~/.openclaw` 配置、凭据和 session 数据. 

**4. 构造 mock tools / fake MCP / fake API**

真实业务评测**不应直接连接生产系统**, 应该提供行为真实、状态可查的假服务: 

- mock CRM
- mock payment
- mock email
- fake MCP server
- sandbox filesystem
- sandbox git repo

**5. 用 plugin hooks 做观测与触发**

```text
session_start        → 绑定 task_id / run_id 到当前 session
before_tool_call     → 记录工具请求 (tool, args)
after_tool_call      → 记录结果 (observation, error, latency)
agent_end            → dump final_state, 触发 evaluator
```

这样评估系统**只观察和校验, 不干预 Agent 推理路径**. 

#### Gateway 是否要改

默认不改. `agent.wait` 已经足够驱动一次 episode 并等待结束. 

只有在以下情况才考虑小范围扩展 Gateway: 

- 需要新增专用 RPC (例如批量 dump state)
- 需要标准化导出生命周期事件
- evaluator 必须通过 Gateway 查询运行态

#### 推荐目录结构

```text
openclaw/
  bench/
    cases/
      refund_basic_001/
      refund_basic_002/
      message_delete_send_001/
      ...
    run_case.ts
    eval_e2e.ts
    eval_process.ts
  plugins/
    business-eval-tools/
      src/
        index.ts
        env.ts
        checker.ts
        tools/
          payment.ts
          message.ts
          order.ts
  extensions/
    qa-lab/
      src/
        state-eval/   # 新增
        scenario-packs.ts
```

#### MVP 路径

不要一开始就建完整框架. 推荐顺序: 

1.**选 1 个低风险任务** (例如"删消息+发消息"). 
2.**构造一个 case 的完整文件**: prompt / initial / target / tools / evaluator. 
3.**写最小 runner**: 加载 case → 起 OpenClaw → 跑完 → dump → 比对. 
4.**跑通一次完整闭环**, 输出 evaluator 结构化结果. 
5.**再扩展到 5–10 个 case** 形成 MVP 评测集. 
6.**之后再做** 统一 schema、CI 集成、影子模式. 

### 4.4 从评测到上线的治理闭环

**执行/状态式评估最终不是为了做一个离线排行榜, 而是为了把 Agent 上线变成可重放、可回归、可门控的工程流程.**

#### 五阶段演进

**阶段一: MVP**

选 5–10 个低风险、高频、终态容易校验的业务任务. 例如 CRM 写备注、订单状态查询、工单分类、邮件草稿生成、简单退款模拟. 目标是跑通"初始态构造 → Agent 执行 → 终态校验 → 报告生成"这条闭环. 

**阶段二: 统一 schema 与 fixture 管理**

所有任务按统一 schema 描述: 任务 ID、初始状态、用户目标、允许工具、目标终态、约束、副作用、evaluator、风险等级. 避免零散脚本堆积. 

**阶段三: CI / nightly 回归**

每次修改 prompt、tool schema、模型版本、OpenClaw agent 配置后, 都自动跑冒烟集; 每天或每周跑完整回归集, 跟踪: 

- 成功率
- 约束通过率
- 副作用率
- 虚假成功率
- 平均工具调用步数
- 总成本

**阶段四: 影子模式 (Shadow Mode)**

Agent 在真实业务流中**只生成候选动作或计划, 不直接写生产系统**, 然后把它的候选动作和人工实际操作对比, 评估"如果让它执行会不会成功". 

这一步可以在**不承担生产写入风险**的情况下收集真实分布数据. 

**阶段五: 小流量受控执行**

按风险等级逐步开放写入权限: 

```text
低风险写入 → 加标签、写备注、生成草稿
中风险写入 → 更新 CRM 字段、创建内部任务
高风险写入 → 退款、发外部邮件、改订单、改生产配置
            (需要更高通过率、更严格红队集、人工确认)
```

#### 权限门控

最终治理方式是**权限门控**: 

- 只读工具、草稿工具、低风险写入工具、高风险写入工具分别对应不同评测门槛
- Agent 通过哪个任务族的状态式评估, 就获得哪个任务族的权限
- 权限不应该来自模型名或主观信心, 而应该来自**在对应任务类型上的执行/状态式评测结果**

#### 自动降级

回归评测跌破阈值时, 自动降级或收回权限. 这是把"评测"和"运行时治理"绑定起来的最后一环, 让评测真正变成生产环境的安全阀. 

---

## 五、局限与开放问题

状态式评估虽然是当前最接近"理想"的 Agent 评估范式, 但它并非银弹. 这一章坦诚讨论它的天花板, 并指向开放的研究方向. 

### 5.1 状态建模成本与可维护性

**问题**: 每个任务族都需要设计 state schema、写校验器、维护 mock service. 任务越多, 维护负担越大. 

**典型表现**: 

- 业务变化时, target_state 字段需要同步更新, 容易遗漏
- 评测集变大后, 校验器代码也变成需要测试的"另一份代码"
- mock service 与真实服务行为漂移, 评测结果失真

**缓解策略**: 统一 evaluator schema、声明式约束语言、自动化 mock 行为录制. 

### 5.2 匹配过严与状态漏字段

**问题**: 两类对称的失败模式. 

- **匹配过严**: exact match 把"功能等价但表述不同"的合理结果判为失败 (例如订单备注多一个标点)
- **状态漏字段**: target_state 没覆盖到的字段就完全不校验, 副作用可能漏过

**缓解策略**: 

- 对自然语言字段用 semantic match 或允许 acceptable variants
- 副作用断言用"白名单原则"  ——  显式列出"允许变化的字段", 其它字段默认不应变化

### 5.3 终态正确但过程违规的盲区

**问题**: Agent 可能用违规路径达到正确终态. 例如: 

- 误删了一个订单, 又重新创建一个看起来一样的订单, 终态匹配但过程违规
- 绕过权限检查直接改数据库, 终态对但行为非法
- 使用了禁止的工具组合 (例如先关安全检查再执行)

**缓解策略**: 

- 增加"过程不变量"断言 (例如"不允许删除现存订单")
- 用 process accuracy 补充
- 关键任务记录完整 trace, 人工抽样审计

### 5.4 难以状态化的目标

**问题**: 有些目标天然难以状态化, 状态式评估无能为力. 

- **审美**: 设计是否好看
- **战略**: 决策是否正确, 需要长期观测
- **开放式写作**: 文章质量
- **长期满意度**: 必须事后观测
- **创造性**: 没有"正确答案"

**缓解策略**: 这类任务必须用文本式或人工评估补充, 状态式评估只能覆盖"可机器校验"的子集. 

### 5.5 与文本式、过程式评估的组合策略

实战中, 三种范式应该组合使用: 

| 场景 | 主评估 | 辅助评估 |
|------|--------|----------|
| 业务执行任务 (退款、下单) | 状态式 | 过程式 (合规检查) |
| 信息查询任务 (RAG / 答疑) | 文本式 | 状态式 (引用是否真实) |
| 工具调用任务 | 状态式 | 过程式 (调用链) |
| 创意/写作任务 | 文本式 (人工 + LLM-judge) | — |
| 复杂 Agent 长程任务 | 状态式 (终态) | 过程式 (milestone) + 文本式 (中间解释) |

**组合的关键**: 每种评估各自回答一个独立维度的问题. 不要试图用一种评估覆盖所有维度. 

### 5.6 开放问题与研究方向

状态式评估远未成熟, 以下是值得投入的研究方向. 

#### 自动化校验器生成

**问题**: 手写 evaluator 成本高, 限制了评测集规模. 

**研究方向**: 

- 用 LLM 从任务描述自动生成 target_state schema
- 从真实业务日志自动提取约束断言
- evaluator 的 program-by-example 合成

#### Partial Credit 设计

**问题**: Hard E2E 过于二值化, soft E2E 又容易刷分. 

**研究方向**: 

- 基于断言重要性加权的 partial credit
- 基于业务影响 (financial impact) 的 partial credit
- 区分"完成核心 + 漏边缘"和"完成边缘 + 漏核心"两类部分完成

#### Proxy Metrics for Non-stateful Goals

**问题**: 审美、战略等目标难以直接状态化. 

**研究方向**: 

- 用代理状态指标近似 (例如用"用户后续行为"代理"用户满意度")
- 多目标评估的 Pareto 前沿分析

#### Process-aware 状态评估

**问题**: 状态式评估容易漏掉"终态对、过程错"的情况. 

**研究方向**: 

- 把过程合规作为一类特殊状态 (例如审计日志状态) 纳入校验
- 状态变化轨迹的合法性校验, 而不仅是终态
- 不变量发现 (invariant mining): 从正确轨迹中自动学习"应该被保持的状态"

#### 长程任务的状态分段校验

**问题**: 长程任务 (几十步) 的终态校验粒度太粗, 中间任何一步出错都导致整体失败, 难以定位. 

**研究方向**: 

- 自动分段: 把长任务切成可独立校验的子目标
- checkpoint-based evaluation: 任务进行中多次状态快照
- 失败定位的二分搜索

#### 评估集污染与泛化性

**问题**: 公开 benchmark 容易被训练数据污染, 评测结果不能反映真实泛化能力. 

**研究方向**: 

- 动态生成评测任务 (从真实业务实时蒸馏)
- 评测任务的"新鲜度"度量
- 反污染评测协议

#### 评估与训练的闭环

**问题**: 当前评估和训练大多脱节, 评估结果不能直接反馈到模型训练. 

**研究方向**: 

- 用状态式评测结果作为 RL 的 reward signal
- failure case 自动转化为训练数据
- 评测驱动的 agent self-improvement

---

## 结语

执行/状态式评估的核心精神是**让 Agent 的能力判断从"看起来能做"变成"在可重放环境中稳定做对, 并且错误可归因、风险可门控"**. 

它不是终极答案  ——  它有清晰的局限, 也有大量开放问题. 但在当前阶段, 它是把 Agent 从 demo 推向生产、从研究推向工程最可靠的桥梁. 

对工程团队而言, 它提供了上线门控的客观依据; 对研究社区而言, 它指出了一个仍在快速演进、充满未解张力的方向. 这两类视角的交汇, 正是这套范式最有价值的地方.