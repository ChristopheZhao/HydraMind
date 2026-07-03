# MAS Production Blog Scenario

这是 HydraMind P0 计划 `PLAN-20260523-001` 的 S52d 场景配置目录。它把
"生产级多智能体系统技术博客" 这个长文任务作为框架最后的真实验证标的：
通过 `hydramind goal` 主路径运行真实 provider + 真实工具（`search.web`,
`image.generate` 等），最终产出一篇约两万字的中文技术博客，并附带一份
红线安全 (redaction-safe) 的证据包，证明 deterministic + semantic
verifier 在生产体感任务下能够正确判定通过/失败。

## 场景目标

1. 通过 `hydramind goal --quality-contract ...` 驱动 goal 主路径完成一次真实
   长文写作（agent 语义 verifier 为默认，无需再开启 flag）；
2. 让产物通过安全/边界确定性校验：产物存在、artifact-root 包含（本地资产
   不得逃逸 artifact_root）、schema 校验；
3. 让产物满足 `semantic_rubric` 中定义的三个语义维度（technical_depth,
   source_grounding, non_mechanical_expression）——这是 agent 的
   verify-good-enough 判定；
4. 收集一份 redaction-safe 的证据包：最终博客 markdown、嵌入的图片、
   JSONL trace、ToolExecution ledger、verifier 结果、planner 诊断。

## 如何运行

### 1. 前置条件

- 已经按 `docs/operations/env-and-live-smoke.md` §1 配好 `.env`
  （`DEEPSEEK_API_KEY`, `KIMI_API_KEY`, `GLM_API_KEY`,
  `BRAVE_SEARCH_API_KEY`, `DOUBAO_API_KEY`）；
- `hydramind doctor env --env-file .env` 全部 `present: true`；
- `hydramind doctor tools --env-file .env --live-tools --tool search.web,image.generate`
  返回 `ok: true`；
- 磁盘可用空间 >= 200MB（artifact 目录、trace、images）；
- 当前网络可正常访问 Brave Search 与 Doubao Image API。

### 2. 一键运行 (推荐)

```bash
bash examples/scenarios/mas-production-blog/run_scenario.sh
```

该脚本会：

- 读取 `goal_spec.json` 生成 `hydramind goal` 的命令行参数；
- 透传 `quality_contract.json`（agent 语义 verifier 默认生效）；
- 透传 `--trace-path`，默认写到同一 run 目录下的 `trace.jsonl`；
- 默认使用较高的 `--max-tool-rounds` 与 `--max-auto-repairs`，支持长文场景
  在 verifier feedback 后继续 repair；
- 把 artifacts 写入 `artifacts/scenarios/mas-production-blog/<timestamp>/`；
- 打印最终 `session_id` 与 artifact_root，供下一步证据采集使用。

依赖：`jq` (优先) 或本机 Python 3.11（脚本会自动回退到内联 Python 解析）。

### 3. 等价手动命令

```bash
.venv/bin/hydramind goal \
  "撰写一篇约 2 万字的中文技术博客..." \
  --provider env --planner auto --live-tools \
  --tool search.web --tool image.generate \
  --tool artifact.write_text --tool artifact.read_text \
  --tool artifact.exists --tool artifact.list \
  --required-tool search.web --required-tool image.generate \
  --expected-artifact blog/mas-production-blog.md \
  --constraint "..." --success-criteria "..." \
  --quality-contract examples/scenarios/mas-production-blog/quality_contract.json \
  --trace-path artifacts/scenarios/mas-production-blog/<run-id>/trace.jsonl \
  --max-tool-rounds 12 --max-auto-repairs 4 \
  --artifact-root artifacts/scenarios/mas-production-blog/<run-id>/ \
  --session-store sqlite --store-path var/blog.sqlite
```

### 4. 收集证据

```bash
python examples/scenarios/mas-production-blog/evidence_collector.py \
  --session-store sqlite --store-path var/blog.sqlite \
  --session-id <session-id> \
  --artifact-root artifacts/scenarios/mas-production-blog/<run-id>/ \
  --trace-path artifacts/scenarios/mas-production-blog/<run-id>/trace.jsonl \
  --output-dir artifacts/scenarios/mas-production-blog/<run-id>/evidence/
```

> 注：JSONL trace 的写出依赖 `JsonlObserver`。`hydramind goal` 已提供
> `--trace-path`，推荐通过 `run_scenario.sh` 统一传入，避免证据包出现
> manifest 有 verifier/ledger 但缺 trace 的假阳性。

## 验收依据

1. **deterministic 部分** — 由 `ContentQualityVerifierRunner` 验证：
   - 文章 unicode 长度 >= `min_length` (18000)；
   - 必含 `required_sections` 列出的 8 个二级标题；
   - 至少有 `min_reference_urls` (5) 个显式 URL；
   - 至少有 `min_image_refs` (2) 个图片引用；
   - 至少有 `min_local_image_refs` (2) 个本地图片引用；
   - 所有本地资产路径解析后落在 `artifact_root` 下。
2. **semantic 部分** — 由 `SemanticArtifactVerifierRunner` 通过
   `ModelProvider` 调度，对 `semantic_rubric.checks` 三项给出 0-1 score；
   每项 score 必须 >= 各自的 `min_score` (0.6)。
   `source_grounding` 要求关键论断附近有 inline URL，不能只在最终参考文献
   中集中列链接。
3. **composite ordering** — 见 `src/hydramind/orchestration/verification.py`
   的 `CompositeVerifierRunner`: 先跑 `TaskContractVerifierRunner`
   (artifact.exists 等)，再跑 deterministic，最后才跑 semantic；任何
   deterministic 失败将短路 semantic 调用，避免烧 token。
4. **repair loop** — 任一 verifier 失败都会通过
   `VerifierFeedbackEvaluator` + `VerifierFeedbackRepairPolicy`
   走正常的 replan/repair 路径，不允许人工脚本干预。

## 证据包结构

```
artifacts/scenarios/mas-production-blog/<run-id>/
├── blog/
│   └── mas-production-blog.md      # 最终交付文章（由 artifact.write_text 写入）
├── assets/
│   └── *.png                       # image.generate 落盘资产
├── trace.jsonl                     # JsonlObserver 写入 (若运行方启用)
└── evidence/                       # evidence_collector.py 产出
    ├── blog.md                     # 文章副本（redaction-safe）
    ├── trace.jsonl                 # trace 副本
    ├── assets/                     # markdown 引用图片副本
    ├── ledger.json                 # ToolExecution 列表 (已 redact)
    ├── verifier_results.json       # 每个 verifier 的 VerifierResult / FeedbackRecord
    ├── planner_diagnostics.json    # ExecutionPlan.metadata.planner_diagnostics + last_plan_delta_diagnostics
    └── manifest.json               # session_id/status/artifact list/sha256/redaction check
```

`evidence_collector.py` 严格使用 stdlib + `hydramind.control` 类型，**不**
导入任何 provider SDK。所有 JSON 输出在写入后会再次扫描，确认没有任何键名
匹配 `api_key|secret|token|authorization|password|content`（大小写不敏感）
的字段泄漏；如果检出，进程以 exit code 2 退出且不会吞掉错误。

## 已知边界

本场景**不**声称解决以下问题，它们属于后续路线图：

- **分布式 worker 正确性** — 当前 lease/heartbeat 仅证明单进程语义正确，
  跨进程/跨节点的租约竞争与 DLQ 尚未做硬约束验证；
- **durable replay / cross-session recovery** — 任何 tool 副作用都没有
  compensating 操作，断电重启后可能产生重复花费；
- **OS / container 沙箱** — `process.run` 当前是 command allowlist，不是
  namespace/cgroup 隔离，不应当作沙箱看待；
- **生产级 broker 可见性 (priority lanes, visibility timeout SLA)** —
  in-memory queue 与现有 worker lease 足够本场景使用，但不替代生产 broker。

凡是这四类问题导致的失败，应按既定运维流程处理，而**不**应靠修改本场景的
quality contract 来"绕过"。
