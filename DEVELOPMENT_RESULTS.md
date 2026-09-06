# RepoPilot Development Results

> RepoPilot 二次开发过程的真实工程记录。面向开发过程、模块实现记录、实验结果、
> 验证证据、Git History、问题追踪。**不是 README。**

## Project Status

Current Phase: Phase 1（Agent Runtime 重构）
Overall Status: IN_PROGRESS
Integration Branch: repopilot-dev
Last Updated: 2026-09-06

| Phase | Module | Branch | Status | Tests | Remote Push |
|------|------|------|------|------|------|
| 0 | Baseline 理解 | chore/phase-00-baseline | COMPLETED（Push BLOCKED，见下） | 13/13 PASS | BLOCKED（无凭据） |
| 1 | Agent Runtime | feat/phase-01-agent-runtime | COMPLETED（Push BLOCKED） | 72/72 PASS | BLOCKED（无凭据） |

---

## Phase 0：理解原始项目

### 1. 开发目标

建立后续所有开发的基线：完整阅读并运行上游 `Windy3f3f3f3f/claude-code-from-scratch`
的 Python 版本（`python/mini_claude/`），梳理 Agent Loop / LLM Backend / Tool
Dispatch / Tool Calling / Streaming / Context Compression / Permission / Budget /
Session / Memory / Skills / Sub-Agent / MCP / CLI 的职责与完整调用链，为 Phase 1
的 Agent Runtime 重构确定"复用什么、抽象什么"。

### 2. 实现内容

- 通读了 `mini_claude` 全部 13 个模块（约 5200 行），重点走读了 agent.py 的
  `chat → _chat_anthropic/_chat_openai → _call_*_stream → _execute_tool_call →
  tools.execute_tool` 调用链与 tools.py 的权限判定链。
- 实际运行了现有测试套件与两次真实 Agent Flow（纯文本回复 + read_file 工具
  调用往返），验证从 User Prompt 到 Tool Result 再返回 LLM 的全过程。
- 输出 [docs/original-architecture.md](docs/original-architecture.md)，
  记录了调用链、逐模块职责、全局状态清单、测试现状、以及 Phase 1 的
  复用/重构接口评估。
- 初始化了 git 仓库（原目录无任何 git 历史），建立
  main → repopilot-dev → chore/phase-00-baseline 分支结构，
  基线提交导入全部原始代码（165 文件，44330 行）。

### 3. 新增文件

| 文件 | 作用 |
|------|------|
| `docs/original-architecture.md` | 原始项目架构分析（Phase 0 主交付物） |
| `DEVELOPMENT_RESULTS.md` | 本文档，开发过程记录 |

### 4. 修改文件

| 文件 | 修改 | 影响 |
|------|------|------|
| `.gitignore` | 增加 `.venv/`、`*.egg-info/` | 防止 65MB 虚拟环境与构建产物入库；不影响代码 |

### 5. 核心设计

原始架构核心（详见 docs/original-architecture.md）：

- 单类 `Agent`（1951 行）承担：LLM 双后端适配、Agent Loop、流式输出、
  4 层上下文压缩、预算、权限、计划模式、子 Agent fork、MCP、会话保存——
  职责过载是 Phase 1 重构的直接动机。
- 工具系统为"模块级全局 list[dict] + 字典表分发"，无注册表、无基类，
  多实例共享全局状态（`_activated_tools`、`_cached_rules`）。
- 权限为纯函数 `check_permission(tool, inp, mode, plan_file) → {action, message}`，
  规则优先级明确，可整体复用为 ACL 内核。
- 子 Agent 通过 fork-return 模式 new 一个独立 Agent 实例执行，已具备
  "多角色可实例化"的雏形，Phase 1 将其固化为 AgentRuntime。

### 6. 测试

真实执行的命令：

```bash
/data/PR/venv/bin/python -m pytest python/tests/ -v
# → 13 passed in 2.80s

# Agent Flow 验证 1：纯文本往返（Anthropic 网关，模型 deepseek-v4-pro[1m]）
cd python && ANTHROPIC_BASE_URL=<网关> python -m mini_claude --model "deepseek-v4-pro[1m]" \
  "Reply with exactly one line: PHASE0_FLOW_OK"
# → 输出 "PHASE0_FLOW_OK"，Tokens: 4131 in / 7 out

# Agent Flow 验证 2：工具调用全链路
python -m mini_claude --model "deepseek-v4-pro[1m]" \
  "Use the read_file tool to read python/mini_claude/__init__.py, then report its version string."
# → 📖 read_file mini_claude/__init__.py → 结果回传 → 最终回复 "版本字符串是 1.0.0"
#   Tokens: 477 in / 193 out, 16640 cached
```

### 7. 验证结果

PASS

```
Passed: 13（test_autonomy 8 + test_autonomy_flow 5）
Failed: 0
Skipped: 0
Execution time: 2.80s
Agent Flow: 2/2 通过（纯文本 + 工具调用链路）
```

### 8. Self-Repair

开发过程中的失败与修复：

- **失败 1**：mini-claude 默认模型 `claude-opus-4-6` 请求网关返回 404。
  Root Cause：`.env` 的 BASE_URL 指向 DeepSeek 网关，模型名不匹配。
  修复：探测网关路径后改用 `deepseek-v4-pro[1m]` 模型 + 正确的
  Anthropic-compatible base URL。
- **失败 2**：`git init -b` 不可用。Root Cause：系统 git 版本 2.25.1。
  修复：`git symbolic-ref HEAD refs/heads/main`。

Repair attempts: 2

### 9. Git 信息

```
Branch:
chore/phase-00-baseline

Commits:
7e5d079 chore: import original claude-code-from-scratch codebase as RepoPilot baseline
a4e6480 docs(phase-00): add original architecture analysis and development results record
331917e chore(phase-00): merge baseline analysis into repopilot-dev

Remote:
none（本地仓库无 origin 配置）

Push Status:
BLOCKED — 无 GitHub 凭据（无 gh CLI、无 credential helper、无 SSH key），
无法完成 git push。按规约第 8 条记录为 IMPLEMENTED_BUT_PUSH_BLOCKED。

Integration:
已合并入 repopilot-dev（本地，merge commit 331917e），合并后回归 13/13 PASS
```

### 10. 当前模块最终实现能力

1. 使用 Python 阅读全部 13 个源模块，梳理出 User Prompt → Agent Loop → LLM →
   Tool Call → 权限 → 分发 → 执行 → 结果 → LLM → 最终回复的完整调用链。
2. 使用真实 LLM 网关运行两次 Agent Flow，验证纯文本与工具调用两条链路均可用
   （含 prompt 缓存命中 16640 tokens）。
3. 使用 git 初始化仓库并建立 main / repopilot-dev / phase 分支结构，基线提交
   导入原始 165 个文件。
4. 使用 pytest 运行原始 13 个测试用例全部通过，确认上游代码健康。
5. 使用架构文档记录全局状态清单与 Phase 1 复用/抽象决策依据。

### 11. 已知问题

- GitHub Push 被阻断（无凭据），所有 Phase 的 "Remote Push" 状态将持续为
  BLOCKED，直到获得可用的 GitHub 认证（token / gh CLI / SSH key）。
- 原始代码无工具层、Agent Loop、权限、MCP、Session 的单元测试（仅有 autonomy
  相关 13 例），Phase 1 将补充 Runtime 单测。
- tools.py 的 `_activated_tools` / `_cached_rules` 是模块级全局状态，多 Agent
  实例间共享，Phase 1 注册表化时需处理。

### 12. 下一阶段依赖

- 供 Phase 1 复用：Agent Loop 本体、tools.py 权限内核（check_permission）、
  subagent 配置、prompt 构造、session/memory/skills/mcp/autonomy 全部模块。
- 已稳定接口：`check_permission(tool, inp, mode, plan_file) -> dict`、
  `execute_tool(name, inp, read_file_state) -> str`、`Agent.chat()/run_once()`。
- 限制：`Agent` 构造参数 11 个且职责过载；双后端消息历史双份维护；
  工具注册是全局 list 拼接。

---

## Phase 1：Agent Runtime 重构

### 1. 开发目标

原始项目只有一个面向 CLI 的 `Agent` 类：11 个构造参数、职责过载（LLM 适配、
工具注册、权限、预算、压缩、子 Agent 全部堆在 1951 行一个类里），工具激活状态
与权限规则缓存是模块级全局变量，多实例共享、互相污染。Phase 1 将其重构为
**可实例化多个角色的通用 Agent Runtime**，为 Phase 5 的 Multi-Agent（Planner /
Explorer / Coder / Tester / Reviewer）打地基：每个角色 = 独立配置 + 独立工具
注册表 + 独立 ACL + 独立预算 + 独立上下文 + 独立事件流。

### 2. 实现内容

新增 `mini_claude/runtime/` 包（10 个模块，约 700 行）：

- **ToolRegistry**：每实例工具注册表，注册/注销/查询/定义渲染/dispatch；
  deferred 工具（enter/exit_plan_mode）的激活状态从模块全局迁入实例级集合，
  修复多实例激活泄漏。
- **Tool 基类接口**：`Tool` dataclass（name/description/input_schema/handler/
  read_only/concurrency_safe/deferred）+ `to_definition()` 渲染 Anthropic 协议
  字典；`tool_from_definition()` 适配器桥接原有 dict 定义。
- **LLMProvider**：后端选择（anthropic/openai）+ 模型能力元数据（context
  window / max output tokens / thinking 支持），`from_config()` 映射
  AgentConfig → Agent 构造参数。
- **AgentConfig**：收敛原 11 个构造参数为带校验的 dataclass（model/权限模式/
  预算/工具集/只读等 13 项规则校验）；`from_role()` 提供 5 个角色档案。
- **ToolACL**：角色级权限层（read_only / allowed_tools / denied_tools），叠加
  在原有 `check_permission` 静态引擎之上；`filter_definitions()` 保证模型永远
  看不到无权使用的工具。
- **Budget**：从 Agent 内联字段抽出的预算对象（同成本公式：$3/M in、0.1x
  cache read、1.25x cache write、$15/M out），record_tokens/check 接口。
- **Context 接口**：后端无关的对话历史视图（Protocol + AgentContext 适配器），
  存储仍复用 Agent 双消息列表。
- **统一 Event + 基础 Trace**：`EventEmitter`（订阅/退订/emit，监听器异常
  隔离）+ 7 种规范事件（run_started/run_finished/llm_request/tool_call/
  tool_result/permission_denied/budget_exceeded）+ `Trace`（时间戳记录 +
  metrics 聚合：llm_calls/tool_calls/denials/tokens/cost/runtime）。
- **AgentRuntime**：组合以上全部；`AgentRuntime(AgentConfig)` 即可得到独立
  运行的 role agent（`planner = AgentRuntime(...)` / `explorer =
  AgentRuntime(...)`），`run(prompt) -> RunResult`。

对原有代码只做外科手术式修改（约 30 行 diff 进 agent.py）：Agent 增加三个
可选挂接点（`_event_emit` / `_tool_dispatcher` / `_active_tool_set`，默认
None → CLI 路径行为完全不变），在工具调用/结果/拒绝/预算超限/LLM 请求处
埋入 `_emit_event`；tools.py 暴露 `BUILTIN_TOOL_HANDLERS` 并让
`get_active_tool_definitions` 接受实例级激活集合。**Agent Loop 本体（流式、
重试、压缩、计划模式、子 Agent、read-before-edit）一行未改。**

### 3. 新增文件

| 文件 | 作用 |
|------|------|
| `python/mini_claude/runtime/events.py` | EventEmitter + 规范事件名 |
| `python/mini_claude/runtime/trace.py` | Trace 事件日志 + metrics 聚合 |
| `python/mini_claude/runtime/budget.py` | Budget / BudgetStatus |
| `python/mini_claude/runtime/config.py` | AgentConfig + 校验 + 角色档案 |
| `python/mini_claude/runtime/provider.py` | LLMProvider 后端/能力抽象 |
| `python/mini_claude/runtime/tools.py` | Tool 基类接口 + 适配器 |
| `python/mini_claude/runtime/registry.py` | ToolRegistry + build_default_registry |
| `python/mini_claude/runtime/permissions.py` | ToolACL 角色权限层 |
| `python/mini_claude/runtime/context.py` | Context 接口 + AgentContext |
| `python/mini_claude/runtime/runtime.py` | AgentRuntime + RunResult |
| `python/mini_claude/runtime/__init__.py` | 公共导出 |
| `python/tests/runtime/test_config.py` | 配置校验测试（12 例） |
| `python/tests/runtime/test_budget.py` | 预算测试（7 例） |
| `python/tests/runtime/test_registry.py` | 注册表测试（11 例） |
| `python/tests/runtime/test_acl.py` | ACL 测试（8 例） |
| `python/tests/runtime/test_events_trace.py` | 事件/Trace 测试（8 例） |
| `python/tests/runtime/test_runtime.py` | Runtime 集成测试（13 例） |

### 4. 修改文件

| 文件 | 修改 | 接口影响 |
|------|------|----------|
| `python/mini_claude/agent.py` | 增加 3 个运行时挂接点 + `_emit_event` + 12 处事件埋点 + 工具分发/激活集合可选覆盖 | 全部为可选、默认关闭；CLI 行为不变 |
| `python/mini_claude/tools.py` | 暴露 `BUILTIN_TOOL_HANDLERS`；`get_active_tool_definitions` 增加关键字参数 `activated`（默认 None = 原全局集合） | 向后兼容 |

### 5. 核心设计

```
AgentConfig (validate) ──┬─→ LLMProvider ──→ Agent 构造参数
                         ├─→ ToolACL (read_only / allowed_tools)
                         ├─→ Budget (cost / turns)
                         └─→ ToolRegistry (实例级激活集合)

AgentRuntime
 ├─ events: EventEmitter ←─ Trace.attach
 ├─ _build_agent(): 原 Agent + ACL 过滤后的 registry.active_definitions()
 │    + agent._event_emit = events.emit
 │    + agent._tool_dispatcher = registry.dispatch(acl=...)
 │    + agent._active_tool_set = registry._activated
 └─ run(prompt): RUN_STARTED → agent.run_once → token 差量入 Budget
      → RUN_FINISHED → RunResult(text, tokens, turns, cost, trace, budget)
```

分发链：模型 tool_call → Agent 循环静态权限检查 → `_tool_dispatcher` →
registry.dispatch →（ACL 检查 → 内置工具走原 execute_tool 保留
read-before-edit；自定义工具走 handler）。

### 6. 测试

真实执行的命令：

```bash
/data/PR/venv/bin/python -m pytest python/tests/runtime/ -v   # 59 passed
/data/PR/venv/bin/python -m pytest python/tests/ -q           # 72 passed（含原 13 例回归）
/data/PR/venv/bin/python -m py_compile python/mini_claude/*.py python/mini_claude/runtime/*.py
# CLI 集成（真实 LLM）：mini-claude → PHASE1_CLI_OK
# Runtime 集成（真实 LLM）：planner + explorer 双实例独立运行 → RUNTIME_ROLE_OK ×2，
#   llm_calls 各 1，planner 工具集 = [read_file, list_files, grep_search, tool_search]
```

### 7. 验证结果

PASS

```
Runtime Unit Tests: 59/59 PASS
Full Regression:    72/72 PASS
Compile check:      PASS
CLI integration:    PASS（真实 LLM 往返）
Dual-role runtime:  PASS（真实 LLM，planner/explorer 独立实例）
Execution time:     ~4s（测试）
```

### 8. Self-Repair

- **失败 1**：LLM mock 测试 `result.text == ''`。Root Cause：stub 整个
  `_call_anthropic_stream` 绕过了流式文本输出（`_emit_text` 在该函数内）。
  修复：改为 fake SDK 边界（`messages.stream` 异步迭代器），让真实流式代码
  参与测试；`RunResult.text` 规范化 strip 流式前导换行。
- **失败 2**：测试文件残留草稿代码（不存在的 `dispatch_sync`、误用
  `tempfile_write_target`）。修复：重写该测试文件。

Repair attempts: 2

### 9. Git 信息

```
Branch:
feat/phase-01-agent-runtime

Commits:
0f1f08c feat(runtime): add event system, trace, budget, config and LLM provider
fa06b38 feat(runtime): add tool interface, per-instance registry, role ACL and context
77822a3 feat(runtime): add AgentRuntime compositing the original agent loop
809abca refactor(agent): add optional runtime integration hooks to the agent loop
c012033 test(runtime): add 59 runtime unit tests
（docs commit 见下方追加）

Remote:
none（本地仓库无 origin 配置）

Push Status:
BLOCKED — 无 GitHub 凭据，按规约第 8 条记录为 IMPLEMENTED_BUT_PUSH_BLOCKED。

Integration:
见下方合并记录
```

### 10. 当前模块最终实现能力

1. 使用 ToolRegistry + Tool 基类接口，实现每实例独立的工具注册、分发与
   deferred 激活（多实例不再互相污染）。
2. 使用 ToolACL + 原 check_permission 引擎，实现角色级只读/工具白名单与
   定义过滤（模型看不到无权工具，运行时调用同样被拒）。
3. 使用 AgentConfig 校验（13 项规则）+ LLMProvider，实现
   `AgentRuntime(AgentConfig)` 一行构造独立角色实例。
4. 使用 Budget 对象，实现 token/成本/轮次记账与超限检测（与 Agent 循环
   共用同一套限制，事件化通知）。
5. 使用 EventEmitter + Trace，实现 7 类规范事件的可观测流与指标聚合
   （llm_calls/tool_calls/denials/tokens/cost/runtime）。
6. 使用 Agent 循环挂接点（默认关闭），在不改动 Loop 本体的情况下把
   原 CLI Agent 升级为多角色 Runtime；LLM mock 经 SDK 边界驱动真实循环
   完成工具往返。

### 11. 已知问题

- GitHub Push 持续 BLOCKED（无凭据），见 Phase 0。
- LLMProvider 目前只覆盖后端选择与模型能力元数据；真实传输仍由 Agent 内部
  双后端代码承担（复用原则），Phase 5 若需要 Provider 级 mock 再扩展
  stream() 接口。
- 事件负载含完整工具结果字符串，大结果（>30KB 落盘）尚未在事件层截断，
  Trace 内存占用风险留待 Phase 9 评估。

### 12. 下一阶段依赖

- Phase 2（Repository Intelligence）可直接复用：AgentRuntime 的 registry
  （注册 symbol/dependency 查询工具）、Tool 接口（新增 repo 工具的基类）、
  ToolACL（Explorer 角色 ACL）、Trace/Budget（repo 扫描任务的观测）。
- 已稳定接口：`AgentRuntime(config).run(prompt) -> RunResult`、
  `ToolRegistry.register/dispatch`、`ToolACL.check/filter_definitions`、
  `AgentEvents.*`、`Budget.record_tokens/check`、`AgentConfig.validate/from_role`。
- 限制：`Agent.chat()` 仍是 CLI 路径的入口；run() 基于 run_once 的
  fork-return 语义（捕获输出，不回显终端）。
