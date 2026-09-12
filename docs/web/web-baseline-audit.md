# Web Baseline Audit（Web Phase 0）

> 生成：2026-09-12 · 分支 feat/web-phase-00-audit
> 依据：《RepoPilot Web 产品化全栈开发执行规约》§28

## 1. 环境审计（Web 化的可执行性）

| 依赖 | 状态 | 结论 |
|---|---|---|
| Python venv（pypi 网络） | ✅ 可用（fastembed/jieba 曾成功安装） | WP2-6 后端依赖可 pip 安装 |
| Node / npm | ❌ 未安装，但 nodejs.org/npm registry 网络可达，无 sudo | WP7 前需用户态安装 node（tarball 到本地目录，PATH 注入） |
| redis-server | ❌ 未安装，无 sudo；anaconda 可用 | WP5 前经 conda-forge 装 redis-server（用户态），或源码构建 |
| Docker | ❌ 无 docker（Phase 8 已如实记录） | WP12 的 compose 实机路径 UNVERIFIED，提供 compose 文件 + 单测锁定 |
| fastapi/uvicorn/redis-py/sqlalchemy/argon2/pytest-asyncio | ❌ venv 未装 | 按 WP2/5/11 各自 Phase 安装 |

## 2. CLI 命令 → Web 迁移审计（核心交付物）

格式：CLI Command → Current Entry → Core Module → Input → Output → Persistence → Web Service Candidate

### repopilot init

- **Entry**：`product/cli.py:212 _cmd_init`
- **Core**：git 检查（`_require_git`）+ 写 `.repopilot/config.json`
- **Input**：dir
- **Output**：终端文本（initialized 路径）
- **Persistence**：`.repopilot/config.json`（JSON 文件，无行级模型）
- **Web Candidate**：`RepositoryService.register()` — 需要从"写文件"改为
  "web 持久化行 + 文件配置双写"（WP3：repositories 表）。

### repopilot index

- **Entry**：`product/cli.py:228 _cmd_index`
- **Core**：`repo/index.py RepositoryIndex.load_or_build()`（load 复用 +
  增量构建 + 保存；多语言 tree-sitter/regex、符号/引用图）
- **Input**：dir、--full
- **Output**：终端统计 + note（files/symbols/imports/references/languages）
- **Persistence**：`.repopilot/index.db`（SQLite，schema 稳定）
- **Web Candidate**：`RepositoryService.index(repo_id, full)` 返回
  IndexBuildResult 结构化数据 + repository_indexes 行（状态/时间/语言）。

### repopilot ask

- **Entry**：`product/cli.py:247 _cmd_ask`
- **Core**：`RepositoryIndex.load_or_build()` + `retrieval.HybridRetriever`
  （BM25+神经语义+结构图+RRF 融合重排 → ContextBuilder）+ AgentRuntime
  LLM 回答
- **Input**：question、--dir、--model、--semantic
- **Output**：终端（semantic backend 标注 + retrieved context + answer）
- **Persistence**：index.db、`.repopilot/embeddings-cache.json`
- **Web Candidate**：`AskService.ask(repo_id, question)` 返回结构化
  evidence（每个 hit 的 file/sources 分数，RetrievalHit.sources 已含
  lexical/semantic/structural/backend label，可直接序列化）+ answer。

### repopilot plan

- **Entry**：`product/cli.py:330 _cmd_plan`
- **Core**：`planning` RequirementParser + Planner（确定性 / --llm）
- **Input**：requirement、--llm、--model
- **Output**：终端 DAG 文本（_print_plan）
- **Persistence**：无（Web 需要 plans 表）
- **Web Candidate**：`PlanningService.create_plan(repo_id, requirement,
  model)` 返回 requirement 元数据 + nodes/edges（TaskDAG 可序列化为
  plan nodes/edges；WP4 必须结构化，不能包终端字符串）。

### repopilot run

- **Entry**：`product/cli.py:416 _cmd_run`（含 `_finish_run` 的 github_flow）
- **Core**：Planner → `product/orchestrator.py:215 run_dag_requirement` →
  `execution/runner.py DagRunner`（并行 worktree、每任务 Agent、真实
  验证、有界自修复、拓扑合并、final verification）→ RunReport →
  `product/github_flow.py`（push/PR/merge/cleanup）
- **Input**：requirement、--dir、--llm、--model、--jobs、--sandbox、
  --task-id、--no-commit、--push/--pr/--merge/--cleanup/--squash
- **Output**：终端（plan 文本 + report.summarize() + github 结果）
- **Persistence**：`.repopilot/runs.jsonl`（RunRecord：role/cost/tokens/
  verification/repair/final）；git worktree/分支；`.repopilot/pr-*.md`
- **Web Candidate**：`RunService.create_run()`（异步 Worker 模式，§12：
  POST → 202 + Redis job → Worker 执行 Core → 事件流）。**关键迁移点**：
  - 长任务必须离开 HTTP 请求（Worker 消费 Redis Streams，WP5）；
  - 事件来源已有现成钩子：`runtime/events.py AgentEvents/EventEmitter`
    （run_started/finished、llm_request、tool_call/tool_result、
    permission_denied、budget_exceeded）——Web 事件 = 订阅 emitter +
    翻译成 RunEvent schema，**不需要解析终端日志**；
  - 取消：DagRunner 现无取消标志——WP5 必须在 task/tool/repair 边界加
    cancellation check（§45：禁止只在 UI 隐藏）；
  - RunLogger 的 runs.jsonl 是聚合记录，Web 需要行级 runs/tasks/
    tool_calls/run_events（WP5/6，SQLite 为 Source of Truth）。

### repopilot issue

- **Entry**：`product/cli.py:432 _cmd_issue`
- **Core**：`product/github.py fetch_issue + issue_to_requirement` → 与
  run 完全相同的 `_run_planned` → github_flow
- **Web Candidate**：不单独建 Service——RunService 的输入通道之一
  （issue 来源），WP 后期可做。

### repopilot graph

- **Entry**：`product/cli.py:469 _cmd_graph`
- **Core**：`RepositoryIndex.load_or_build()` → `index.graph` 模块依赖
  边打印
- **Output**：终端（modules/edges 文本）
- **Web Candidate**：`GraphService.graph(repo_id, root, depth, node_type,
  limit)` — §18 要求分页加载（大图禁止一次全量）；现有 DependencyGraph
  （模块级）+ ReferenceGraph（符号级）都可作为数据源。

### repopilot benchmark

- **Entry**：`product/cli.py:486 _cmd_benchmark`
- **Core**：`evaluation/harness.py:60 retrieval_eval`（temp 仓库 24 任务
  × 5 检索栈，真实运行）+ `metrics.aggregate`；agentic_eval 存在但 CLI
  未接线
- **Output**：终端每栈 recall/MRR + 原始 JSON
- **Persistence**：`.repopilot/benchmark/retrieval_results.json`
- **Web Candidate**：`BenchmarkService.run_benchmark(repo_id)` 异步
  （Worker），结果入 benchmark_runs 表 + 结构化 metrics（复用
  TaskRunMetrics.to_dict），UI 用 Recharts 展示真实数据（§19 禁止
  伪造）。注意：benchmark 构建 temp 任务仓库（build_task_repos）——
  保留该机制，Web 只是包装 + 持久化。

## 3. 复用 / 迁移原则结论（无损迁移判据）

1. **CLI 与 Web 共享 Core 已可行**：所有命令的核心逻辑都不在 CLI 层
   （cli.py 只做 argparse + 打印 + 组装），直接可抽 Application Service。
2. **Service 层契约**：现有函数全部不 print（print 都在 cli.py），
   返回结构化对象（IndexBuildResult/RunReport/DagRunReport/TaskRunMetrics
   /RetrievalHit 均为 dataclass）——符合 §9 "Service 不打印"要求，抽取
   成本低。
3. **事件流钩子已存在**：runtime AgentEvents（8 类事件）覆盖
   agent/tool/llm 粒度；DAG 层（task.ready/started/completed、
   verification、repair、merge）目前只在报告聚合，WP5 需在
   DagRunner/run_dag_requirement 插入事件发布点（改动集中在 runner.py
   的 outcome 状态转换处）。
4. **持久化现状**：index.db（稳定 schema）+ runs.jsonl（追加日志）+
   JSON 文件。Web 需新增应用库（repositories/plans/runs/tasks/
   run_events/tool_calls/verification/repair/benchmark_runs/users/
   approvals，§14），SQLAlchemy 2.0 + SQLite，与 index.db 分开
   （`.repopilot/app.db`）。
5. **诚实缺口（Web 化必须补）**：
   - 无取消机制（§45）；
   - 无 approval/human-in-the-loop 等待（§44 事件存在但无流程）；
   - agentic benchmark 未接 CLI/Web；
   - runs.jsonl 无 event 级记录（tool_call 细节在 recorder 里聚合后
     丢失，Web 需要逐事件落库）；
   - plan/run 无行级持久化（现仅内存 + 文件）。

## 4. 现状确认：已实现 vs 仍为设计（Web 规约视角）

| 能力 | 状态 |
|---|---|
| Core（仓库智能/检索/规划/多代理/worktree/验证/沙箱/评测） | ✅ 真实实现（545/545 测试，FINAL_ASSESSMENT.md） |
| CLI | ✅ 真实实现（§25 要求保留并回归） |
| Application Service 层 | ⚠️ 逻辑已内聚，但未抽取成独立 Service 类（WP1） |
| FastAPI / REST | ❌ 未实现（设计） |
| Redis Streams / Worker / SSE | ❌ 未实现（设计） |
| Web 持久化 | ❌ 未实现（设计） |
| React 前端 | ❌ 未实现（设计） |
| 认证 | ❌ 未实现（设计） |
| Docker Compose 部署 | ❌ 未实现（设计；本机无 docker，实机 UNVERIFIED 预期） |

## 5. Baseline 测试与 Smoke

- 全量回归：545/545 passed in 451.55s（2026-09-12，真实运行）。
- CLI Smoke（本审计复核，2026-09-12 真实输出）：

```
$ venv/bin/python -m mini_claude.product.cli index
  index: loaded /data/PR/RepoPilot/.repopilot/index.db + re-parsed 9 changed file(s) → saved

$ venv/bin/python -m mini_claude.product.cli graph
  (index: loaded /data/PR/RepoPilot/.repopilot/index.db (218 files) — up to date)
  modules: 354  edges: 977

$ venv/bin/python -m mini_claude.product.cli plan "把 discount 计算从 order 移到 pricing"
  requirement: ... (kind=...)
  --- plan ---
  - T-F-1 [coder]: Implement the feature
  - T-F-2 [tester]: Add/verify tests (deps: T-F-1)
  - T-F-3 [reviewer]: Review the change (deps: T-F-2)
  3 task(s)
```

  其余命令（ask/run/issue/benchmark）的历史真实运行见
  DEVELOPMENT_RESULTS.md Phase 11-17。

## 6. Web 化 Baseline 结论

每个 CLI 命令都能无损迁移到 Application Service（判据：核心逻辑在
core 模块、无 print 耦合、返回结构化对象）；事件流可复用 runtime
EventEmitter；长任务需要 Worker+Redis（本环境 redis 需用户态安装）；
前端需用户态 node。按规约顺序 WP1 → WP12 推进。
