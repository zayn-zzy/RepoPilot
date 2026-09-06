# Mini Claude Code — Python 版

与 TypeScript 版功能 99% 一致的 Python 实现。**需要 Python >= 3.11**。

> 📖 完整教程文档见 [claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch)（文档中所有代码块均支持 TypeScript / Python 切换）

## 快速开始

```bash
# 安装（需要 Python 3.11+）
cd python
pip install -e .

# 设置 API Key
export ANTHROPIC_API_KEY=sk-ant-...

# 运行
mini-claude-py "hello"               # 一次性模式
mini-claude-py                       # 交互式 REPL
mini-claude-py --yolo "list files"   # 跳过确认
mini-claude-py --plan "refactor this" # 计划模式
python -m mini_claude "hello"        # 也可以用 python -m 方式运行

# 使用 OpenAI 兼容后端
OPENAI_API_KEY=sk-xxx mini-claude-py --api-base https://api.openai.com/v1 --model gpt-4o "hello"
```

## 文件结构

| Python 文件 | 对应 TypeScript | 说明 |
|-------------|----------------|------|
| `agent.py` | `agent.ts` | Agent 核心循环、双后端、4 层压缩 |
| `tools.py` | `tools.ts` | 10 个工具 + 5 种权限模式 |
| `__main__.py` | `cli.ts` | CLI 入口与 REPL |
| `ui.py` | `ui.ts` | 终端 UI（rich） |
| `prompt.py` | `prompt.ts` | 系统提示词构造 |
| `session.py` | `session.ts` | 会话管理 |
| `memory.py` | `memory.ts` | 记忆系统 |
| `skills.py` | `skills.ts` | 技能系统 |
| `subagent.py` | `subagent.ts` | 子 Agent |
| `frontmatter.py` | `frontmatter.ts` | YAML frontmatter 解析 |
| `runtime/` | —（RepoPilot 新增） | 多角色 Agent Runtime（见下） |

## RepoPilot Agent Runtime（Phase 1）

`mini_claude/runtime/` 将原 CLI Agent 包装为**可实例化多个角色的通用运行时**：

```python
from mini_claude.runtime import AgentRuntime, AgentConfig

planner = AgentRuntime(AgentConfig.from_role("planner", api_key="..."))
result = await planner.run("分析这个仓库的结构")  # -> RunResult
```

每个实例独立拥有：ToolRegistry（工具注册/分发/延迟激活）、ToolACL（角色级
只读/工具白名单）、Budget（成本/轮次）、Context（对话历史视图）、
EventEmitter + Trace（7 类规范事件与指标聚合）。内置角色档案：
planner / explorer / reviewer（只读）、coder（读写）、tester（读 + shell）、
general（全部工具）。Agent Loop 本体（流式、压缩、权限、子 Agent）未改动，
CLI 行为与原来一致。

## 依赖

- `anthropic` — Anthropic SDK（流式）
- `openai` — OpenAI SDK（兼容后端）
- `rich` — 终端彩色输出
