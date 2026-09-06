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
| 1 | Agent Runtime | feat/phase-01-agent-runtime | IN_PROGRESS | - | - |

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
（Phase 0 文档提交见下方追加记录）

Remote:
none（本地仓库无 origin 配置）

Push Status:
BLOCKED — 无 GitHub 凭据（无 gh CLI、无 credential helper、无 SSH key），
无法完成 git push。按规约第 8 条记录为 IMPLEMENTED_BUT_PUSH_BLOCKED。

Integration:
已合并入 repopilot-dev（本地）
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

（开发完成后填写）
