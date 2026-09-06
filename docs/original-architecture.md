# 原始项目架构分析（Phase 0 交付物）

> 本文档是 RepoPilot 二次开发 Phase 0 的产物：对上游
> `Windy3f3f3f3f/claude-code-from-scratch` Python 版本（`python/mini_claude/`）
> 的完整架构梳理。目标是让后续 Phase 能"从代码层面解释 User Prompt 到 Tool
> Result 再返回 LLM 的全过程"，并明确哪些代码可复用、哪些需要重构。

---

## 1. 概览

`mini_claude` 是一个与 TypeScript 版功能 99% 对齐的最小 Claude Code 克隆，
约 5200 行 Python，无框架依赖（仅 anthropic / openai / rich 三个 SDK），
`requires-python >= 3.11`。功能覆盖：双 LLM 后端、流式输出、10+ 工具、
5 种权限模式、4 层上下文压缩、计划模式、子 Agent、Skills、Memory（语义召回）、
MCP、CLI/REPL、`/goal` `/loop` 等自主性扩展。

```
mini_claude/
├── __main__.py      CLI 入口 + 交互 REPL（350 行）
├── agent.py         Agent 核心循环（1951 行）★ 最大文件
├── tools.py         工具定义 + 分发 + 权限（740 行）
├── prompt.py        系统提示词构造（266 行）
├── session.py       会话 JSON 持久化（49 行）
├── memory.py        文件型记忆 + 语义召回（382 行）
├── skills.py        .claude/skills/*/SKILL.md 技能系统（171 行）
├── subagent.py      子 Agent 类型定义（171 行）
├── mcp_client.py    stdio JSON-RPC MCP 客户端（250 行）
├── ui.py            rich 终端 UI（212 行）
├── frontmatter.py   YAML frontmatter 解析（47 行）
└── autonomy.py      /goal /loop Auto Mode 纯函数（482 行）
```

---

## 2. 完整调用链：User Prompt → Tool Result → LLM

这是理解整个项目的关键路径（以 Anthropic 后端为例）：

```
CLI (__main__.py)
  └─ Agent.chat(user_message)                      agent.py:439
       ├─ [首次] McpManager.load_and_connect()     懒加载 MCP 工具，拼入 self.tools
       ├─ _chat_anthropic(user_message)            后端分派
       │    ├─ _push_anthropic_user_message()      首次用户消息嵌入 <system-reminder>
       │    ├─ _check_and_compact()                85% 阈值自动压缩
       │    ├─ _start_memory_prefetch_for_turn()   语义记忆召回（sideQuery 并行预取）
       │    └─ while True:                          ← Agent Loop 本体
       │         ├─ _run_compression_pipeline()    Tier1 预算 / Tier2 snip / Tier3 microcompact
       │         ├─ _consume_memory_prefetch_if_ready()  轮询预取结果注入
       │         ├─ _call_anthropic_stream()       ★ LLM 调用（流式）
       │         │    ├─ 参数: system(cache_control 断点) + tools + messages(滚动缓存断点)
       │         │    ├─ _with_retry()             429/503/529 指数退避重试
       │         │    └─ content_block_stop 回调 → 并发安全工具提前启动
       │         ├─ token 统计（input/output/cache_read/cache_creation 分开记账）
       │         ├─ 解析 tool_use blocks
       │         ├─ 无 tool_use → print_cost() → break（最终回复）
       │         └─ 有 tool_use:
       │              ├─ current_turns += 1 → _check_budget()（成本/轮次上限）
       │              └─ 逐个 tool_use:
       │                   ├─ 权限: check_permission(name, inp, mode, plan_file)
       │                   │    └─ deny → tool_result="Action denied"
       │                   │    └─ confirm → _confirm_dangerous() 人工确认
       │                   ├─ _execute_tool_call(name, inp)        ★ 工具分发
       │                   │    ├─ enter/exit_plan_mode → 计划模式状态机
       │                   │    ├─ agent → _execute_agent_tool()  子 Agent（fork-return）
       │                   │    ├─ skill → _execute_skill_tool()  技能（inline/fork）
       │                   │    ├─ mcp__* → McpManager.call_tool()
       │                   │    └─ 其余 → tools.execute_tool()    字典表分发到 _read_file 等
       │                   ├─ _persist_large_result()  >30KB 落盘 + 预览替换
       │                   └─ tool_results 追加为 user 消息 → 回到 while 循环
  └─ (循环退出后) _auto_save()                      会话 JSON 持久化
```

### 关键设计点

1. **双消息历史**：`_anthropic_messages` 与 `_openai_messages` 各自独立维护，
   按后端切换。会话保存/恢复/压缩分别实现两套（这是 Phase 1 要抽象的点）。
2. **前缀缓存**：Anthropic 后端把 system 拆成 static（cache_control 断点）+ dynamic
   两段；消息数组用 `_with_cache_breakpoints()` 在**副本**上打滚动断点，持久历史
   不混入 API 元数据。
3. **流式工具提前执行**：`content_block_stop` 事件触发时，若工具在
   `CONCURRENCY_SAFE_TOOLS` 且权限自动放行，立刻 `asyncio.create_task` 启动，
   模型还在生成时工具已在跑。
4. **read-before-edit**：`_read_file_state` 记录 文件绝对路径→mtime，
   写/改前必须读过且 mtime 未变。
5. **4 层压缩**：Tier1 长结果预算截断（利用率 >0.5）→ Tier2 过期结果 snip
   （>0.6，缓存热且 <0.75 时豁免）→ Tier3 microcompact（空闲 5 分钟后清旧结果）
   → 85% 时 LLM 摘要压缩。

---

## 3. 逐模块分析

### 3.1 Agent（agent.py）

单类 `Agent`，构造参数已接近一个 Config 对象（11 个 kw-only 参数）：

```python
Agent(permission_mode, model, api_base, anthropic_base_url, api_key,
      thinking, max_cost_usd, max_turns, confirm_fn, custom_system_prompt,
      custom_tools, is_sub_agent)
```

职责过载问题（Phase 1 重构动机）：

| 职责 | 位置 | 问题 |
|------|------|------|
| LLM 后端适配 | 每个 API 调用处 `if use_openai else anthropic` 双分支 | 无 LLMProvider 抽象 |
| 工具注册 | `self.tools` 是一个裸 `list[dict]`，运行时被 MCP/loop 直接 `+` 拼接 | 无 Tool Registry |
| 工具分发 | `_execute_tool_call` if/elif 链 | 与 tools.py 的字典表分发割裂 |
| 权限 | 调用 tools.check_permission，Auto Mode 走 LLM 分类器 | 已模块化，但无 ACL 概念 |
| Budget | `_check_budget()` 内联 | 无 Budget 对象 |
| 上下文 | `_anthropic_messages/_openai_messages` 双份 | 无 Context 接口 |
| 事件/输出 | `print_*` 直接调用 ui 模块，子 Agent 用 `_output_buffer` 捕获 | 无事件系统 |
| 子 Agent | `_execute_agent_tool` 里重新 new 一个 Agent（fork-return） | 已具备"多实例"雏形 |
| 观测 | token 计数 + print_cost | 无 Trace 结构 |

可复用资产：Agent Loop 本体（流式、压缩、重试、read-before-edit、plan 模式、
sub-agent fork）、autonomy（/goal /loop Auto Mode 分类器）、prompt 构造。

### 3.2 工具系统（tools.py）

- **定义**：`tool_definitions: list[ToolDef]`，`ToolDef = dict`（Anthropic schema
  dict，含 name/description/input_schema，可选 `deferred` 标记延迟加载）。
- **分发**：模块内 `handlers: dict = {"write_file": _write_file, ...}` 字典表。
  `execute_tool()` 为 async，实际 handler 全为同步函数。
- **权限**：`check_permission(tool, inp, mode, plan_file)` 返回
  `{"action": allow|deny|confirm, "message"}`。优先级：deny 规则 > plan 模式
  只读约束 > bypassPermissions > allow 规则 > 读工具放行 > acceptEdits 编辑放行
  > 危险命令/新文件 confirm > dontAsk 自动拒绝。
- **规则加载**：`~/.claude/settings.json` + `cwd/.claude/settings.json` 的
  `permissions.allow/deny` 列表，支持 `Tool(pattern)` 语法，带进程级缓存。
- **危险命令**：`DANGEROUS_PATTERNS` 正则（rm/git push/sudo/reboot...）。
- **缺陷**：注册表是模块级全局状态（`_activated_tools`、`_cached_rules`），
  多 Agent 实例之间共享且互相污染；无 Tool 基类，MCP/skill/agent 等"伪工具"
  散落在 agent.py 的 if 链里。

### 3.3 Prompt（prompt.py）

`SYSTEM_PROMPT_TEMPLATE` 内嵌完整系统提示词。static/dynamic 拆分：
`build_static_system_prompt()`（纯模板，可缓存）+ `build_dynamic_system_context()`
（平台/git/记忆索引/技能/自定义 agent/延迟工具名）。CLAUDE.md + 日期通过
`build_user_context_reminder()` 注入首条用户消息。CLAUDE.md 支持 `@includes`。

### 3.4 Memory（memory.py）

文件型 4 类记忆（user/feedback/project/reference），目录按 cwd hash 隔离
（`~/.mini-claude/projects/<hash>/memory/`）。语义召回：sideQuery（temp=0 小调用）
读 30 行 frontmatter 清单，选 ≤5 条注入；`start_memory_prefetch` 在每 turn 开始时
异步预取，settle 后注入下一条 LLM 消息，带 60KB 会话注入预算。

### 3.5 Skills（skills.py）

`.claude/skills/<name>/SKILL.md`，frontmatter 支持 description / when_to_use /
allowed-tools / user-invocable / context（inline|fork）。fork 型在 agent.py 里
spawn 子 Agent 执行。

### 3.6 Sub-Agent（subagent.py）

内置 explore / plan / general 三种类型 + `.claude/agents/*.md` 自定义（frontmatter
+ allowed-tools）。配置返回 `{system_prompt, tools}`，由 agent.py fork-return 模式
实例化新的 Agent 执行。**注意**：子 Agent 只是"更小的工具集 + 定制提示词"，
不是独立运行时。

### 3.7 MCP（mcp_client.py）

stdio JSON-RPC（无 SDK），`mcp__server__tool` 前缀命名，读取
`~/.claude/settings.json` / `.claude/settings.json` / `.mcp.json`。主 Agent 首次
chat 时懒连接，工具定义追加到 `self.tools`。

### 3.8 Session（session.py）

`~/.mini-claude/sessions/<id>.json` 存双后端消息历史 + metadata。

### 3.9 CLI（__main__.py）

argparse + 6 种权限 flag + 一次性/REPL 双模式；REPL 内实现 `/clear /plan /cost
/compact /goal /loop /memory /skills /<skill>`。

### 3.10 Autonomy（autonomy.py）

纯 stdlib 函数：/goal 的 Stop-hook 评估器（GOAL_EVALUATOR_SYSTEM + verdict 解析）、
/loop 的间隔/动态自步进（schedule_wakeup 工具）、Auto Mode 两阶段分类器
（rules JSON + 剧本构建 + block 判决解析）。有跨语言 golden fixture 测试。

---

## 4. 数据流与状态总结

| 状态 | 载体 | 作用域 |
|------|------|--------|
| 消息历史 | `_anthropic_messages` / `_openai_messages` | Agent 实例 |
| Token/成本 | Agent 实例字段 | Agent 实例 |
| 工具列表 | `self.tools`（模块全局 `tool_definitions` 的引用或拷贝） | 实例引用、全局污染风险 |
| 已激活延迟工具 | tools._activated_tools | **模块全局**（多实例共享） |
| 权限规则缓存 | tools._cached_rules | **模块全局** |
| 读文件状态 | `_read_file_state` | Agent 实例 |
| 已确认路径 | `_confirmed_paths` | Agent 实例 |
| 记忆索引 | 文件系统 | 按 cwd hash 隔离 |
| 技能/自定义 agent | 模块级缓存 | **模块全局** |

## 5. 测试现状

```
python/tests/test_autonomy.py      8 个 golden fixture 用例（纯 stdlib，unittest）
python/tests/test_autonomy_flow.py 5 个 Auto Mode 两阶段分类器控制流用例（IsolatedAsyncio）
```

共 13 个用例。使用 `python3 -B python/tests/test_autonomy.py` 或 pytest 运行。
**无工具层、Agent Loop、权限、MCP、Session 的单元测试。**

## 6. Phase 1 重构接口评估

| 原始代码 | 结论 |
|----------|------|
| Agent Loop（流式/重试/压缩/plan/sub-agent） | **复用**，作为 AgentRuntime 的执行引擎 |
| tools.py 分发 + 权限 | **复用逻辑**，包进 ToolRegistry / Tool 接口 |
| `Agent.__init__` 11 个参数 | 收敛为 AgentConfig |
| 双后端 if/else | 抽象 LLMProvider |
| `_check_budget` | 抽成 Budget 对象 |
| 双消息历史 | 抽 Context 接口（保持两份存储不变，接口统一） |
| print_* 直调 ui | 抽 EventEmitter，ui 作为默认订阅者 |
| token 统计 | 抽 Trace（Run/ToolCall/LLMRequest 事件） |
| autonomy / memory / skills / session / mcp | **原样保留**，Phase 1 不触碰 |
