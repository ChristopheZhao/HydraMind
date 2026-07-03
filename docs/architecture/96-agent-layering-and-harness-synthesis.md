# 96 — Harness:定义、边界,及其与各模块的区别与联系

> 状态:harness-engineering 概念收尾文档(主线 = Harness 的定义与边界)。
> 日期:2026-06-21。负责人:zhaojj。
> 主线:厘清 **Harness 的定义、边界,以及它与 scaffold / orchestration / control
> plane / runtime / framework / loop 的区别与联系**,并给可操作判据。
> 附线:以两个重构分支(trackB / main)作为「用该模型评估」的一个**案例**(§9)。
> 全文标注「共识」与「仍有争议」,并区分「采纳自他人」与「本项目综合」。本文不改代码。
>
> **决策已采纳(2026-06-21,用户确认 — `90-decisions.md` ADR-0012):** 本项目正式
> 立 **§2/§3 的窄义 harness(data-plane 执行器;propose-vs-commit;spawn → orchestration;
> harness 只 emit 委派请求)** 为立场,并按「**配置 vs 实例化**」调和(harness 拥有 sub-agent
> *配置/策略* + 发委派请求;orchestration 拥有 *spawn/实例化*,control 落账)。因此 §9/§10 里
> 「trackB `spawn_subagent` 落位待核」已结论为**既定 Phase-2 重构**(把 spawn 从 harness 面
> 上移到 orchestration),非待定。三维评估理由见 ADR-0012。本文移植自 track1,作为该决策的
> 支撑综合;`95-execution-harness-correction.md` 在与本文冲突处由本文取代。

---

## 1. 目的与范围

「harness」一词在 2026 年未收敛:有人指整个产品、有人指评测脚手架、有人混同
framework/SDK/orchestrator。本文为 HydraMind 定一个**有意的、可操作的**定义,并把它
与周边模块的边界一次性钉清。所有结论附判据与出处;凡属领域分歧,标注我们选的边。

核心方法:不纠结「harness 这个词」,改用**功能分层**(一个 agent 系统由哪些层叠起来)
与**构建方式**(用什么手段搭这些层)两根**正交轴**来归位,再用四个判定问题区分组件。

---

## 2. Harness 的定义(窄义)

> **Harness = data-plane 执行器:真正去执行单 agent 一拍的那部分 —— 跑 loop、调模型、
> 派发工具、产生副作用 —— 由 Scaffold 配置其行为,向上「提案状态 / 请求委派」,
> 自身从不落账 durable 状态、也从不 spawn(实例化)agent。**

判定问题(用它替代词义之争):
- 「它会自己调模型 / 跑工具 / 改外部世界吗?」**会 → 它在 harness(data plane)。**

定义选型说明:这是**窄义**。声量更大的主流(LangChain「非模型即一切」;Anthropic 自己
的「effective harnesses」里 harness 会写 `claude-progress.txt` + git commit;Firecrawl;
MongoDB)用**广义** harness,把 scaffold 与 durable 状态都折进去。只有 Hugging Face 术语表
(2026-05)完整背书窄义切分。**我们取窄义**,因为它与 HydraMind 现有代码一致
(`AGENTS.md`:harness = LLM-SDK 边界;治理 = control + gates),歧义最小 —— 但每处都要
声明「此处 harness ≠ LangChain 广义」。

---

## 3. Harness 的边界(做什么 / 不做什么)

| Harness **做** | Harness **不做**(归谁) |
|---|---|
| loop 控制结构(ReAct-as-loop) | 定义行为/推理模式(→ Scaffold,是输入) |
| 调模型、派发工具、组装 context | 落账 durable 状态(→ Control,单写者) |
| 产生副作用 | 授权 / 门控(→ Gates) |
| emit 提案 + 证据 + **委派请求** | 协调 / 实例化 agent / spawn(→ Orchestration) |

**两条不变量(规范性选择,带对立标注):**
1. **harness 提案状态、请求委派;从不落账 durable 状态。** 落账归 control plane。
2. **harness 不 spawn(实例化)agent;** 即便「决定委派」在 loop 内以 tool-call 出现,
   实例化仍归 orchestration。

> 诚实标注:这两条**可辩护但有争议**。支持:Faramesh「execution control plane」
> (2601.17744)「Reasoning Space vs Execution Space」+ 动作改变现实前的强制
> permit/deny/defer;The Agent Stack Part 3「runtime emit 新状态,control plane 决定这次
> run 是什么」。反对:主流 SDK 是 **loop 经 tool-call 直接 spawn/委派**(Claude Agent
> SDK「subagent 能 spawn 自己的 subagent」、OpenAI handoffs-as-tools、CrewAI delegation
> tools);Golem Cloud(2026-06)主张持久化**与**授权应在 per-agent runtime 内部。
> **我们仍选分离**,因为 HydraMind 要做可持久、可恢复、control-owned 的 native MAS ——
> 这套分离正是让 sub-agent 成为一等协调单位、并让 crash/restart 恢复成为可能的前提。

---

## 4. Harness 与各模块的区别与联系

### 4.1 vs Scaffold —— 行为 vs 执行(互补,非对立)

- **Scaffold** = agent 怎么「想」:系统提示、工具描述、输出格式、推理模式。
- **Harness** = 这套想法怎么被稳定执行。
- 关键澄清:**ReAct 作为「提示模式」= Scaffold;ReAct 作为「循环实现」= Harness。**
- 联系:scaffold 是喂给 harness 的**行为配置**,不是它的对立层;换模型时 scaffold 要调、
  harness 基本不变(Life-Harness 2605.22166 佐证:harness 可跨模型骨干移植)。
- 注:scaffold 中的角色/契约部分也属稳定 MAS 契约(`AgentSpec`),不是纯执行输入。

### 4.2 vs Orchestration —— 执行 vs 协调

- **Harness** 跑**单** agent 一拍;**Orchestration** 决定谁/何时/拓扑/协议/**spawn**。
- 边界:harness **提案/请求**(含「我想委派」),**orchestration 拥有协调与 spawn**。
- 依据:Macedo 2606.10106「harness 可含 orchestration,但定义它的是 adaptive loop」,
  且固定图 orchestrator **不是** harness。即便广义 harness 含 orchestration,spawn 也归
  其 orchestration **子函数**,非 reasoning loop(LangChain《Anatomy》也把 subagent
  spawning 单列为「Orchestration Logic」)。
- 对立(已标注):主流 SDK loop 经 tool 直接 spawn(见 §3)。

### 4.3 vs Control plane(Governance / Gates / State)—— 执行 vs 契约/落账

- **Harness** 干活并提案;**Control plane** 立契约、授权、落账。
- 三对应:harness 提案状态 → Control 落账(单写者);harness 跑工具 → Gates 授权;
  durable interaction / turn / lease → Control 拥有。
- 一句区分:**harness「执行」治理并提案,control plane「拥有」状态并落账。**
- 依据:`AGENTS.md`「只有 control 改 RuntimeSession;gates 第一类」;
  `execution/contracts.py`「proposed to, but not applied by, Control」。

### 4.4 vs Runtime —— 最不稳的词,慎用

- 术语裂缝:**Credal(2026-04)反转** —— harness = 赋能、runtime = 约束。表面与我们
  相反,实则可对齐:**Credal 的 runtime ≈ 我们的 control plane(约束层),Credal 的
  harness ≈ 我们的 harness + scaffold(应用层赋能)。**
- 另一些来源(IBM、Golem)又把 runtime 当 data-plane 执行层、或把执行+治理融进一个
  runtime。
- 结论:**「runtime」承重歧义最大,文档里优先用 harness(执行器)/ control plane,
  慎用裸 runtime**;若用,必须注明取哪种切法。

### 4.5 vs Framework —— 正交轴(配置 vs 编码)

- **Framework 不在功能分层轴上**。MindStudio(2026-05):「用 framework 你写 agent,
  用 harness 你配置 agent。」
- framework/SDK 是**构建方式轴**(用现成抽象搭 vs 裸搭),横切所有层。可以**用一个
  framework 去搭一个 harness**,也可在 Claude Code 上**裸搭** harness。
- 正确问法不是「用 harness 还是 framework」,而是「用哪种方式搭你的 harness」。

### 4.6 vs Loop —— 子集关系

- **loop ⊂ harness**:Macedo 把「一个 adaptive loop」列为 harness 四个必要充分要素之一,
  故 loop 不可能在 harness 之上。
- 「循环」一词至少三义,用时须标明:(a) 内层 ReAct 循环(harness 的实现核);
  (b) 外层长程自驱循环(Osmani 的「loop engineering」;他把它放 harness 之上,是因为
  用了窄义 harness);(c) 自演化循环(Self-Harness 2606.09498 那种「重写 harness 的
  循环」)。

---

## 5. 主刀:control plane vs data plane(+ 递归两尺度)

把上面所有区别归到一把刀上:**control plane(决策/授权/协调,不碰世界)vs data plane
(执行,碰世界)**。这是**新兴共识**(IBM、ETCLOVG、Futurum、TrueFoundry),我们采纳、
不据为己有。

```
CONTROL PLANE  — 决策 / 授权 / 协调
  ├─ Orchestration  (协调面):谁 / 何时 / 拓扑 / 协议 / spawn
  └─ Governance+Gates+State (契约面):allowed? / 落账 / durable interaction、turn-lease、单写者
        ▲ harness 提案 / 请求          │ control 授权 / 落账 / spawn
        ▼
DATA PLANE  — 执行
  └─ Harness(执行器:loop·模型·工具·副作用)← 由 Scaffold 配置
```

**递归两尺度(本项目综合,无在先工作):** 单 agent 内 —— 本地 gates=control、loop=data;
MAS 间 —— orchestration+durable interaction=control、**每个 agent(scaffold+harness+本地
control+turn)= 一个 data-plane 单位**;`turn-lease` 是两尺度接缝。

一处保留:NVIDIA AI Red Team「单个 prompt 内 control 与 data 不可分」否定的是**信息论**
切分,不是**架构**切分;我们只取架构切分。

---

## 6. 映射到 HydraMind(只是命名 —— 不重建)

| 平面 / 脸 | 概念 | HydraMind |
|---|---|---|
| 控制面 · 协调 | Orchestration | `orchestration/` + `mas/` |
| 控制面 · 契约 | Governance/Gates/State | `control/` + `gating/` + `governance/` |
| 数据面 · 执行器 | Harness | `providers/` + 单 agent loop + `tools/` runner |
| 数据面 · 配置 | Scaffold | prompts(config) + `AgentSpec.instructions`/`prompt_ref` |

完整一拍:Orchestration 选 agent → Control 发 turn-lease → Scaffold 配置行为 → Harness
跑这一拍(每个 tool call 过门控),emit 提案+证据+委派请求 → Gates/Governance 授权,
Control 落账 → 若请求委派,Orchestration 实例化 sub-agent 为新协调单位(递归)。现有
不变量已编码这套,故 Phase-2 是改名/边界收口,非新架构。

---

## 7. settled vs 仍有争议(诚实校准)

| 已厘清(我们的一致选择) | 领域仍争(我们选了边) |
|---|---|
| scaffold = 行为配置、喂 harness | 「harness」窄 vs 广 —— 取**窄义**并标注(主流广义) |
| harness = 执行器(data plane);loop ⊂ harness | 「harness 提案 / control 落账与 spawn」—— **规范性**;Golem + SDK loop-spawn 反对 |
| orchestration 拥有 spawn/协调 | control/data 切分 —— 采纳为**共识**(非原创) |
| framework 是正交构建轴、非某一层 | 「runtime」一词归属 —— 多源相反,我们慎用 |
| 用四个判定问题,不靠「harness」歧义词 | 递归两尺度 —— **我们的综合**,无在先工作 |

**「我们的贡献」只保留两件事:** (a) 把 *scaffold* 提为一等命名层;(b) control/data 切分
的递归两尺度。其余「following emerging consensus」,引用不据为己有。

---

## 8. 研究基线(2025-10 ～ 2026-06,已核实)

论文:Harness-Bench(2605.27922)、HarnessFix(2606.06324)、Macedo「What makes a harness
a harness」(2606.10106,「harness 可含 orchestration,但定义它的是 adaptive loop」)、
Life-Harness(2605.22166)、Self-Harness(2606.09498)、Meta-Harness(2603.28052,
Stanford/UW-Madison —— **非 MIT**)、Agentic Harness Engineering(2604.25850,复旦 ——
标题「Agentic」非「Agent」)、AdaptOrch(2602.16873,模型趋同时拓扑方差 Ω(1/ε²) 压过
模型选择)、Faramesh(2601.17744)、Five-Plane runtime governance(2606.12320)。

业界/实践:Anthropic「Effective harnesses for long-running agents」(2025-11-26)、
LangChain「Anatomy of an Agent Harness」(2026-03-10)+「harness engineering」
(2026-02-17,只改 harness、模型固定 → terminal-bench +13.7)、Hugging Face agent 术语表
(2026-05-25,唯一背书窄义切分)、MindStudio(2026-05-01,framework=编码/harness=配置)、
Credal(2026-04-21,harness 赋能/runtime 约束)、browser-use「Bitter Lesson of Agent
Harnesses」(2026-04-19)、IBM「Agent Control Plane」、VentureBeat「control vs execution」、
Futurum ACPF(2026-04-03)、The Agent Stack Part 3(2026-04-13)、Golem Cloud「Rise of the
Agent Runtime」(2026-06-10,融合 runtime 对立立场)、Martin Fowler/Böckeler
(2026-04-02,反对堆叠分层)。

谱系(窗外,背景):control/data plane 源自网络/K8s;ETCLOVG 与「T1 loop 是构成件」一脉;
监督控制/引用监视器/shielding 是「外层约束信封」之下更老的理论。

来源注意:多为灰色文献、快速演进、若干预印本带未来日期/未来模型名、未经同行评审;厂商
框法有动机把自己卖的那层说成决定性那层。**不要引用**无一手源的「70%/65% 来自模型之外」
等数字。Futurum 页对自动抓取 403,外引前用浏览器核实。

---

## 9. 案例:用该模型评估两个重构分支(trackB / main)

> 本节是**附线** —— 把 §2–§5 的模型当尺子,去评估 HydraMind 两个独立重构分支,
> 兼作原始任务(分支质量评估)的答复。

两分支从锚点 `4d62ebc` 各自独立实现 `PLAN-20260618-001`(S0–S7)。

| | main(`hm/s11-trace-console`) | trackB(`hm/exec-harness-refactor-trackB`) |
|---|---|---|
| 范围 | S0–S7 | S0–S7 + `PLAN-20260619-001` N1–N5 |
| diff | 130 文件,+9061/−976 | 190 文件,+16925/−2296 |
| 结构 | 顶层 `providers/`、`execution/`;**保留** `HarnessBackend` | provider 在 `harness/`;**删除** `HarnessBackend`;加 `ExplicitSubmitExecutionHarness`(原名 `ReActExecutionHarness`,见 ADR-0012 rename note) |
| 工具链 | ruff/mypy 净,pytest 795 | ruff/mypy 净,pytest 854 |
| 评分 | ~77/100(B+) | ~87/100(A−) |

- **各自完成:** 两者 S0–S7 达标(S3 typed 协议产出、S4 内存授权+durable repair
  budget+幂等、S5 durable interaction+turn-lease+resume)。trackB 另删 `HarnessBackend`
  (不变量守护)并以真实第二 harness + e2e swap 证明可替换。
- **共有问题:** broad typed policy 宽在类型、薄在承载(生产传空 policy)。main:mock 仅
  别名 shim 未物理搬;trackB:ADR-0009 正文现在时滞后、`compact_context` 是 NotImpl。
- **用本模型看:** trackB 的 `ExplicitSubmitExecutionHarness`(原名 `ReActExecutionHarness`,
  ADR-0012 rename note)正是 §4.6 的「act/observe-as-loop = harness 执行核」的一次落地,其
  e2e swap 证明的是**执行单元可替换**。命名收窄的理由也由本模型推出:可换轴是 harness,故壳应
  按壳层变量(loop 粒度 + 终止契约)命名,而 ReAct 是 agent/scaffold 层、跨壳可移植的范式
  (config 非壳身份),不应命名壳。而它把 `spawn_subagent` 放在 harness 上(§4.2)曾是命名/
  边界 smell,已由 phase-2(PLAN-20260622-001)落位到 orchestration。
- **决定性差距:** harness 可替换性 —— main 名义、trackB 真实。**结论:以 trackB 为交付
  主干;从 main 仅 pick ADR/真相面写法。** 注:live 验收未在此独立复跑,sign-off 前补一次。

---

## 10. 阶段计划与决策待办

**Phase-1(交付 trackB,轻量收口):** pick main 的 ADR 写法;修 ADR-0009(现在时→过去时)
+ guard 扩到 ADR 正文;收窄「可替换」措辞(指执行单元/loop-as-harness);跑一次 full live
留 artifact;zhaojj sign-off 后标完成。

**Phase-2(校准,不重建):** 采纳 §2–§4 词汇 + 四判定问题为项目词表;改名/重定位使 spawn
归 orchestration、harness 只 emit 委派请求(核 trackB `spawn_subagent`);把载重 policy
字段接到默认 harness 或删掉;落地或 P1 标注 `compact_context`;定清单 agent 是否走同一套
interaction/turn 机制;重写 `95-execution-harness-correction.md` 被本文取代处。

> **Phase-2 残项收尾(PLAN-20260623-001):**
> - **单 agent 节点是否走同一套 interaction/turn 机制?——已核实并钉死(B)。**
>   结论:**在 durable 记录层面是「否」。** durable interaction 记录(S5a:
>   `DurableInteractionRecorder` / `DurableInteraction`)只接在 **team 路径**
>   (`CollaborationExecutor` → `collaboration_team.py`;`agent.py` 传
>   `durable_recorder=self._control`);单 agent dispatch 路径
>   (`agent_invocation.py` 的 DIRECT / SUBAGENT)**不记 durable interaction**。
>   ADR-0007「plain node = single-member interaction」是**内核/调度模型**的统一
>   陈述,**不等于** durable 记录层的对等 —— S5a 是 team-scoped、record-only。
>   契约测试 `test_durable_interaction_recording_is_team_scoped_not_single_agent`
>   钉住两半(team 有、单 agent 无),防止未来悄悄宣称对等或悄悄移除。
> - **载重 policy 收口 ——「裁剪 inert 字段」(A,用户确认,ADR-0010 §F 对齐)。**
>   §10 上面「把载重 policy 字段接到默认 harness 或删掉」已**取删**:`ExecutionHarnessPolicy`
>   裁掉全部悬空 `*_ref` carrier(及只剩 ref / 是活策略死镜像的子策略
>   prompt-context / memory / tool-environment / evaluation / observability),
>   只留 harness 真正自持的旋钮 `multi_turn` / `constraints` / `recovery` /
>   `subagents`。判据:悬空 ref = 隐含一个不存在的 resolver 的债;自包含旋钮 =
>   harness-owned 策略的合法类型化表达(ADR-0010 §F)。`ExecutionConstraints` 文档
>   强化为「declared ≠ enforced」(沙箱/容器实施在 tool-sandbox 层)。契约 guard
>   `test_execution_harness_policy_carries_no_unresolved_ref`(软口径:递归无
>   `*_ref` + 正向钉 `constraints.max_turns` 仍是活读点)防悬空指针回流。
> - **C:** `95-execution-harness-correction.md` 已加 SUPERSEDED/HISTORICAL 抬头并标注被本文取代处。

**留给负责人:** 确认 §2/§3 的「窄义 harness + propose/commit」为项目立场 —— 明知是对抗
Golem/loop-spawn 主流的少数派/规范性选择,选它因为 HydraMind 的价值是可持久、可恢复、
control-owned 的 native MAS。

---

## 11. 出处声明

两平面框架取自 ETCLOVG / IBM / Futurum;propose-vs-commit 不变量取自 Faramesh 与 The
Agent Stack;scaffold/harness 区分取自 Hugging Face 术语表;framework/harness 正交轴取自
MindStudio;runtime 反转取自 Credal。**递归两尺度切分**与**把 scaffold 提为一等命名层**是
本项目自身综合。对立立场(Golem 融合 runtime;loop-spawns-via-tool 的 SDK 主流;广义
harness 约定;Fowler 反对堆叠)作为「有意选择反对」记录在案,不是疏漏。
