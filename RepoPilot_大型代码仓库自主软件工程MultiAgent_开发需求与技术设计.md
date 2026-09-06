# RepoPilot：面向大型代码仓库的自主软件工程 Multi-Agent
## 基于 `claude-code-from-scratch` 的二次开发完整需求与技术设计文档

> 文档定位：项目立项 + 系统设计 + 开发路线 + 测试评测 + 简历包装  
> 推荐实现语言：Python 3.11+  
> 原始底座：`Windy3f3f3f3f/claude-code-from-scratch`  
> 项目暂定名：**RepoPilot**  
> 项目类型：Coding Agent / Multi-Agent / Software Engineering Agent / Agent Harness  
> 目标场景：大型代码仓库中的 Issue-to-Patch / Requirement-to-PR 自主软件工程任务  
> 文档版本：v1.0（2026-09-05）

---

# 0. 一页项目概览

## 0.1 项目一句话定义

**RepoPilot 是一个面向大型代码仓库的自主软件工程 Multi-Agent 系统：以 Coding Agent Harness 为底座，通过仓库结构理解、混合代码检索、Task DAG、多智能体协作、Git Worktree 隔离、测试驱动验证与自动修复，实现从自然语言需求 / GitHub Issue 到代码修改、测试验证和 Patch/PR 生成的端到端自动化。**

---

## 0.2 为什么做这个项目

传统 Mini Claude Code 项目通常重点实现：

- Agent Loop
- Tool Calling
- 文件读写
- Shell 执行
- 上下文压缩
- Memory
- Skills
- Sub-Agent
- MCP

这些能力可以证明开发者理解了 **Agent Harness**，但如果直接作为简历项目，容易被评价为：

> “复现开源项目，工程增量有限。”

RepoPilot 的二次开发目标不是继续堆 Tool，而是解决 Coding Agent 在大型代码仓库中的几个关键问题：

1. **仓库规模过大，无法直接放入 Context Window；**
2. **单纯 Grep / Vector RAG 难以准确定位跨文件依赖代码；**
3. **复杂需求无法由单 Agent 在单一上下文中稳定完成；**
4. **并行 Agent 修改同一仓库容易产生代码冲突；**
5. **LLM 生成代码并不意味着代码可编译、可测试、可合并；**
6. **缺少可量化 Benchmark，无法证明 Agent 改造是否真的有效。**

因此，本项目的核心技术主线是：

```text
Agent Harness
    +
Repository Intelligence
    +
Hybrid Code Retrieval
    +
Task DAG Orchestration
    +
Multi-Agent Collaboration
    +
Git Worktree / Docker Sandbox
    +
Verification-driven Self-Repair
    +
SWE-bench Evaluation
```

---

## 0.3 项目最终能力

理想的端到端工作流：

```text
用户需求 / GitHub Issue
        │
        ▼
Requirement Understanding
        │
        ▼
Repository Intelligence
(AST / Symbol / Dependency / Semantic Index)
        │
        ▼
Hybrid Retrieval
(Lexical + Semantic + Structural)
        │
        ▼
Planner Agent
        │
        ▼
Task DAG
        │
 ┌──────┼─────────┐
 ▼      ▼         ▼
Explore  Code    Test
Agent    Agent   Agent
 │        │        │
 └───────┼────────┘
         ▼
Git Worktree / Docker Sandbox
         │
         ▼
Reviewer Agent
         │
         ▼
Verification & Self-Repair
         │
   ┌─────┴─────┐
   │           │
 PASS         FAIL
   │           │
   │           └──> Diagnose -> Repair -> Re-test
   ▼
Patch / Commit / Pull Request
```

---

# 1. 原始项目底座分析

## 1.1 原始项目

GitHub：

`https://github.com/Windy3f3f3f3f/claude-code-from-scratch`

本项目采用其 **Python 版本**作为学习和二次开发底座。

原项目当前已经覆盖一个 Coding Agent 的核心 Harness 能力：

- Agent Loop
- Anthropic / OpenAI Compatible LLM Backend
- Streaming Response
- Tool Calling
- 并行只读 Tool Execution
- Streaming Tool Early Start
- 文件 Read / Write / Edit
- Grep / Glob
- Bash
- Web Fetch
- Tool Search
- Plan Mode
- Permission / Safety
- Context Compression
- Session Persistence
- Memory
- Skills
- Sub-Agent
- MCP
- Cost / Turn Budget
- Retry / Error Recovery
- CLI

这意味着 RepoPilot 不需要重新证明：

> “LLM 如何调用 Bash。”

二次开发应该重点放在：

> “Agent 如何真正理解和修改大型软件工程项目。”

---

## 1.2 原项目 Python 结构

原始 Python 版本大致为：

```text
python/
├── mini_claude/
│   ├── agent.py
│   ├── tools.py
│   ├── autonomy.py
│   ├── __main__.py
│   ├── ui.py
│   ├── prompt.py
│   ├── session.py
│   ├── memory.py
│   ├── skills.py
│   ├── subagent.py
│   ├── mcp_client.py
│   ├── frontmatter.py
│   └── system_prompt.md
└── pyproject.toml
```

原项目代码量有限，模块职责清晰，适合作为二次开发底座。

---

## 1.3 原有模块与 RepoPilot 的复用关系

| 原模块 | 是否复用 | RepoPilot 中的演进 |
|---|---|---|
| `agent.py` | 高度复用 | 重构为通用 Agent Runtime |
| `tools.py` | 高度复用 | 拆分 Tool Registry + Tool Implementations |
| `prompt.py` | 复用 | 增加角色化 Prompt 和 Repository Context |
| `session.py` | 复用 | 增加 Task / Agent / Run 级持久化 |
| `memory.py` | 部分复用 | 扩展为 episodic / semantic / repository memory |
| `skills.py` | 复用 | 软件工程 Skill 包 |
| `subagent.py` | 核心复用 | 升级为 Multi-Agent Runtime |
| `mcp_client.py` | 复用 | GitHub / issue / CI / external tool 接入 |
| `autonomy.py` | 部分复用 | 与 Task DAG / autonomous execution 合并 |
| CLI | 复用 | 增加 repo / issue / benchmark 命令 |
| Context Compression | 核心复用 | 增加代码上下文价值评分 |
| Permission | 核心复用 | 增加 Agent Role Tool ACL |
| Budget | 核心复用 | 增加 Task / Agent 级预算 |

---

# 2. 项目目标与边界

## 2.1 核心目标

RepoPilot 需要支持以下核心任务：

### G1：理解大型代码仓库

Agent 能够建立：

- 文件树
- 编程语言分布
- Package / Module
- Class / Function / Method Symbol
- Import / Dependency
- Call Graph
- Test Mapping
- Git History Metadata

避免每次任务都从头 Grep 整个仓库。

### G2：从需求定位相关代码

针对：

> “修复登录 Token 刷新失败问题”

能够从需求中识别关键实体，并找到：

- Auth Controller
- User Service
- Token Service
- JWT Utils
- Repository
- Tests
- Configuration

而不是仅靠关键词命中。

### G3：复杂任务自动拆解

将复杂需求拆成带依赖关系的 Task DAG。

### G4：Multi-Agent 分工

至少实现：

- Planner Agent
- Explorer Agent
- Coding Agent
- Test Agent
- Reviewer Agent

### G5：安全修改代码

所有写操作运行于：

- 独立 Git Branch / Worktree
- 可选 Docker Sandbox

禁止 Agent 无边界修改宿主机。

### G6：自动验证与修复

代码修改必须经过：

- lint
- compile/type-check
- unit test
- targeted test
- regression test
- review

失败后进入 Self-Repair。

### G7：量化评测

必须能够在：

- 自建任务集
- SWE-bench Lite / Verified（按资源情况）

上进行测试。

---

## 2.2 非目标

第一版不建议追求：

- 完全复制 Claude Code
- 自研大模型
- 自研 Embedding 模型
- 完整 IDE
- 生产级 Kubernetes Agent 平台
- 一开始就支持所有编程语言
- 一开始实现几十个 Agent
- 一开始实现复杂 Swarm 共识协议

第一阶段优先支持：

> **Python Repository**

第二阶段再扩展：

- TypeScript / JavaScript
- Java
- Go

---

# 3. 项目技术定位

## 3.1 项目关键词

简历 / GitHub README 推荐围绕以下关键词描述：

- Autonomous Software Engineering Agent
- Coding Agent Harness
- Multi-Agent
- Context Engineering
- Repository Intelligence
- Structural Code Retrieval
- Hybrid Retrieval
- Task DAG
- Agent Orchestration
- Git Worktree
- Docker Sandbox
- Self-Repair
- MCP
- SWE-bench

---

## 3.2 技术栈

### 核心语言

```text
Python 3.11+
```

### LLM

建议统一定义 `LLMProvider`：

```text
Anthropic API
OpenAI API / OpenAI-compatible API
可选本地模型：vLLM / Ollama
```

避免系统绑定单一模型。

### 数据模型

```text
Pydantic v2
dataclasses
```

### Repository Parsing

第一版：

```text
tree-sitter
tree-sitter-python
```

后续：

```text
LSP
Jedi / Pyright（Python）
tsserver（TypeScript）
jdtls（Java）
gopls（Go）
```

### Graph

```text
NetworkX
```

规模更大时：

```text
Neo4j（可选）
```

### Semantic Retrieval

第一版：

```text
sentence-transformers / OpenAI Embedding
FAISS
```

工程化升级：

```text
Qdrant
```

### Lexical Retrieval

```text
ripgrep
BM25
rank-bm25
```

### Git

```text
Git CLI
GitPython（可选）
Git Worktree
```

### Sandbox

```text
Docker
```

### API

可选：

```text
FastAPI
```

CLI 仍然是第一入口。

### Persistence

MVP：

```text
SQLite
```

后续：

```text
PostgreSQL
Redis
```

### Observability

```text
Python logging / structlog
OpenTelemetry
```

### Evaluation

```text
pytest
SWE-bench harness
Docker
pandas
```

---

# 4. 总体系统架构

## 4.1 分层架构

```text
┌─────────────────────────────────────────────────────────┐
│                  Interface Layer                        │
│ CLI / API / GitHub Issue / MCP                          │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│               Orchestration Layer                       │
│ Requirement Parser / Planner / Task DAG / Scheduler     │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                    Agent Layer                           │
│ Explorer | Planner | Coder | Tester | Reviewer          │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                Agent Runtime Layer                       │
│ Agent Loop / LLM / Tool Dispatch / Permission           │
│ Context / Memory / Skills / Budget / Session            │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│            Repository Intelligence Layer                │
│ AST / Symbol / Import Graph / Call Graph / Retrieval    │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                  Execution Layer                         │
│ File / Shell / Git / Test / Worktree / Docker           │
└──────────────────────────┬──────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────┐
│                Observability Layer                       │
│ Trace / Token / Cost / Events / Evaluation Metrics      │
└─────────────────────────────────────────────────────────┘
```

---

# 5. 核心模块一：Agent Runtime 重构

## 5.1 目标

将原项目中的 Agent Loop 从：

> “一个 CLI Coding Agent 的主循环”

重构成：

> “可以实例化多个不同角色 Agent 的通用 Runtime”。

---

## 5.2 Agent Definition

建议定义：

```python
class AgentConfig(BaseModel):
    name: str
    role: str
    system_prompt: str
    model: str
    tools: list[str]
    max_turns: int
    max_cost: float | None = None
    read_only: bool = False
    context_policy: str = "default"
```

---

## 5.3 Agent Runtime

```python
class AgentRuntime:
    config: AgentConfig
    llm: LLMProvider
    tools: ToolRegistry
    context: ContextManager
    memory: MemoryManager
    permission: PermissionManager
    tracer: AgentTracer

    async def run(self, task: AgentTask) -> AgentResult:
        ...
```

---

## 5.4 Tool Registry

将原始大 `tools.py` 拆为：

```text
tools/
├── registry.py
├── base.py
├── file/
│   ├── read.py
│   ├── write.py
│   └── edit.py
├── search/
│   ├── grep.py
│   ├── glob.py
│   └── semantic.py
├── shell/
│   └── bash.py
├── repository/
│   ├── symbol.py
│   ├── dependency.py
│   └── code_search.py
├── git/
│   ├── diff.py
│   ├── status.py
│   ├── commit.py
│   └── worktree.py
└── test/
    ├── pytest_tool.py
    └── lint.py
```

Tool 必须包含：

```python
name
description
json_schema
read_only
risk_level
timeout
execute()
```

---

## 5.5 Tool ACL

不同 Agent 只能使用指定工具。

例如：

### Explorer Agent

```text
read_file
grep
glob
symbol_search
dependency_search
semantic_search
git_log
```

禁止：

```text
write
edit
commit
```

### Coding Agent

允许：

```text
read
search
edit
write
bash
git_diff
```

### Reviewer Agent

默认只读。

这样可以形成简历技术点：

> Role-based Tool Access Control。

---

# 6. 核心模块二：Repository Intelligence

这是整个二次开发项目最重要的技术模块之一。

## 6.1 目标

大型代码仓库不能完全注入 LLM Context。

因此需要建立仓库级结构化索引：

```text
Repository
    │
    ├── Files
    ├── Modules
    ├── Symbols
    ├── Imports
    ├── Dependencies
    ├── Calls
    ├── Tests
    └── Git Metadata
```

---

## 6.2 Repository Scanner

输入：

```text
repo_path
```

输出：

```python
RepositoryMetadata
```

包括：

```python
class RepositoryMetadata(BaseModel):
    root: str
    languages: dict[str, int]
    files: list[FileMetadata]
    modules: list[ModuleMetadata]
    test_files: list[str]
    config_files: list[str]
```

扫描时必须自动忽略：

```text
.git
node_modules
venv
.venv
dist
build
target
__pycache__
vendor
large binaries
generated files
```

---

## 6.3 AST Parser

使用 Tree-sitter 抽取：

```text
class
function
method
import
call expression
assignment
decorator
docstring
```

形成：

```python
class Symbol:
    id: str
    name: str
    type: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None
    parent_symbol: str | None
```

---

## 6.4 Symbol Index

支持：

```text
find_symbol("TokenService")
find_definition("refresh_token")
find_references("UserService.login")
find_tests_for_symbol(...)
```

Symbol Index 是比纯向量数据库更适合代码理解的结构化入口。

---

## 6.5 Dependency Graph

节点：

```text
File
Module
Class
Function
```

边：

```text
IMPORTS
CALLS
IMPLEMENTS
INHERITS
REFERENCES
TESTS
```

第一阶段可以用 NetworkX：

```python
G = nx.DiGraph()
```

例如：

```text
auth/controller.py
      │
      ▼ IMPORTS
auth/service.py
      │
      ▼ CALLS
token/service.py
      │
      ▼ CALLS
jwt/utils.py
```

---

## 6.6 增量索引

大型仓库不能每次完整重新索引。

设计：

```text
file hash
mtime
git diff
```

仅重新解析发生变化的文件。

流程：

```text
Repo Changed
    ↓
git diff --name-only
    ↓
Changed Files
    ↓
Re-parse AST
    ↓
Update Symbol Index
    ↓
Update Graph
    ↓
Update Embedding
```

这会成为项目的重要工程亮点。

---

# 7. 核心模块三：Hybrid Code Retrieval

## 7.1 为什么不能只做 Vector RAG

代码搜索中存在三种不同类型的信息：

### Lexical

例如：

```text
refresh_token
TokenService
JWT_SECRET
```

Grep / BM25 非常有效。

### Semantic

例如用户描述：

> “登录凭证过期后自动获取新的访问凭证”

代码可能没有出现“自动获取新凭证”这句话。

需要 Embedding。

### Structural

如果定位到：

```text
AuthController.login()
```

还应该考虑：

```text
AuthService
TokenService
UserRepository
Tests
```

需要 Dependency Graph。

因此采用：

> Lexical + Semantic + Structural Hybrid Retrieval

---

## 7.2 Retrieval Pipeline

```text
User Requirement
      │
      ▼
Query Analyzer
      │
      ├─────────────┬──────────────┐
      ▼             ▼              ▼
Lexical         Semantic       Structural
Retrieval       Retrieval      Expansion
      │             │              │
      └─────────────┼──────────────┘
                    ▼
              Candidate Merge
                    │
                    ▼
                 Rerank
                    │
                    ▼
              Context Builder
```

---

## 7.3 Retrieval Score

建议：

```math
Score(d,q) =
α * Lexical(d,q)
+ β * Semantic(d,q)
+ γ * Structural(d,q)
+ δ * Recency(d)
+ ε * TestRelation(d)
```

第一阶段：

```text
α = 0.25
β = 0.35
γ = 0.30
δ = 0.05
ε = 0.05
```

权重最终通过实验调节。

---

## 7.4 Structural Expansion

例如检索首先定位：

```text
TokenService.refresh()
```

向 Dependency Graph 扩展 1~2 hop：

```text
AuthService.login()
JWTUtils.verify()
TokenRepository.save()
test_token_refresh()
```

这通常比单纯增加 Vector Search Top-K 更有效。

---

## 7.5 Reranker

MVP：

规则 + weighted score。

第二阶段：

使用 LLM 或 Cross-Encoder：

```text
Query + Symbol/File summary
        ↓
Relevance Score
```

---

# 8. 核心模块四：Context Engineering

## 8.1 Context 分类

不能把所有内容简单拼接。

建议 Context Manager 维护：

```text
System Context
Task Context
Repository Context
Retrieved Code Context
Conversation Context
Memory Context
Tool Result Context
Execution Context
```

---

## 8.2 Token Budget

示例：

```text
System / Agent Role             8%
Task / Plan                    10%
Repository Summary             8%
Retrieved Code                35%
Recent Conversation           12%
Memory                         7%
Tool Results                  10%
Output Reserve                10%
```

不是固定比例，应动态调整。

---

## 8.3 Context Value Score

给每个 Context Chunk 打分：

```math
CV_i =
w1 * SemanticRelevance
+ w2 * StructuralRelevance
+ w3 * TaskDependency
+ w4 * Recency
+ w5 * UsageFrequency
- w6 * Redundancy
```

然后按：

```text
Value / Token
```

排序。

---

## 8.4 与原有四层压缩结合

保留底座已有的渐进式压缩思想：

```text
Budget Truncate
      ↓
Stale Snip
      ↓
Micro Compact
      ↓
Auto Compact
```

RepoPilot 增加：

```text
Repository-aware Context Selection
```

形成：

```text
Retrieve → Rank → Budget → Compress → Inject
```

---

# 9. 核心模块五：Requirement Understanding

## 9.1 输入

系统至少支持：

```text
自然语言需求
GitHub Issue
Bug 描述
Feature Request
Stack Trace
测试失败信息
```

---

## 9.2 Requirement Schema

```python
class Requirement(BaseModel):
    title: str
    description: str
    task_type: Literal[
        "bugfix",
        "feature",
        "refactor",
        "test",
        "docs"
    ]
    constraints: list[str]
    acceptance_criteria: list[str]
    suspected_components: list[str]
```

---

## 9.3 Requirement Agent 输出

例如：

```json
{
  "task_type": "bugfix",
  "goal": "fix token refresh failure",
  "constraints": [
    "do not change public API"
  ],
  "acceptance_criteria": [
    "expired access token can be refreshed",
    "existing login tests remain passing"
  ]
}
```

---

# 10. 核心模块六：Planner Agent 与 Task DAG

## 10.1 为什么需要 DAG

复杂任务可能有：

- 可并行任务
- 强依赖任务
- 验证任务
- 修改后才能执行的任务

因此不能只生成普通 TODO List。

---

## 10.2 Task 数据模型

```python
class TaskNode(BaseModel):
    id: str
    title: str
    description: str
    agent_role: str
    dependencies: list[str]
    status: str
    priority: int
    files: list[str] = []
    max_turns: int = 10
```

---

## 10.3 状态机

```text
PENDING
   │
   ▼
READY
   │
   ▼
RUNNING
   │
   ├──────────► FAILED
   │
   ▼
COMPLETED
   │
   ▼
VERIFYING
   │
   ├──────────► REPAIR
   │
   ▼
VERIFIED
```

---

## 10.4 DAG 示例

需求：

> 增加 JWT Refresh Token。

```text
T1 分析现有认证模块
       │
       ├──────────────┐
       ▼              ▼
T2 实现 Token       T3 增加 Config
Service              │
       │              │
       └──────┬───────┘
              ▼
        T4 修改 Auth API
              │
              ▼
        T5 添加 Unit Test
              │
              ▼
        T6 Integration Test
              │
              ▼
        T7 Reviewer
```

---

## 10.5 Scheduler

Scheduler 找到：

```text
所有 dependencies 已完成的 Task
```

并进入 Ready Queue。

如果多个 Task：

- 没有文件冲突
- 没有依赖关系
- 资源预算允许

可以并行执行。

---

# 11. 核心模块七：Multi-Agent

## 11.1 Agent 角色

### 1. Planner Agent

职责：

- Requirement 分析
- Repository Summary 阅读
- Task DAG 生成
- Task 重规划

权限：

```text
read-only
```

---

### 2. Explorer Agent

职责：

- Code Search
- Symbol Search
- Dependency Analysis
- 找测试
- 找配置
- Root Cause 初步分析

特点：

```text
低成本模型 + 大量只读工具调用
```

---

### 3. Coding Agent

职责：

- 根据 Task 修改代码
- 最小化 diff
- 保持代码风格
- 生成必要测试

---

### 4. Test Agent

职责：

- 选择测试命令
- 执行 targeted tests
- 执行 regression tests
- 解析错误

---

### 5. Reviewer Agent

职责：

检查：

```text
Correctness
Regression
API Compatibility
Security
Maintainability
Test Coverage
Unexpected Diff
```

默认只读。

---

## 11.2 Agent Communication

不要让 Agent 之间直接无限聊天。

采用结构化 Artifact：

```python
class AgentArtifact(BaseModel):
    task_id: str
    agent: str
    summary: str
    findings: list[str]
    files: list[str]
    evidence: list[str]
    recommendation: str | None
```

Agent 通过 Artifact Communication，而不是共享全部 Context。

优点：

- 降低 Token
- 降低上下文污染
- 可持久化
- 可追踪
- 易调试

---

# 12. 核心模块八：Git Worktree Isolation

## 12.1 问题

多个 Agent 同时修改：

```text
repo/
```

会：

- 冲突
- 覆盖文件
- 状态不可恢复
- diff 不可追踪

---

## 12.2 方案

每个写 Task 创建：

```bash
git worktree add ...
```

结构：

```text
repo/
worktrees/
├── task-T2/
├── task-T3/
└── task-T4/
```

每个 Agent：

```text
独立 working directory
独立 branch
独立 Git diff
```

---

## 12.3 Merge Strategy

Task 完成：

```text
Task Branch
    ↓
Tests
    ↓
Reviewer
    ↓
Commit
    ↓
Merge Coordinator
    ↓
Conflict Detection
```

冲突时不要自动暴力覆盖。

进入：

```text
MERGE_CONFLICT
```

由专门 Repair / Merge Agent 处理。

---

# 13. 核心模块九：Docker Sandbox

## 13.1 使用场景

Agent 可以执行：

```text
pip install
npm install
pytest
shell command
build
```

因此最好支持：

```text
Docker Sandbox
```

---

## 13.2 Sandbox Policy

需要限制：

```text
CPU
Memory
Timeout
Disk
Network
Environment Variables
Mounted Directory
```

严禁默认把：

```text
SSH Key
Cloud Credential
Personal Token
Host Docker Socket
```

注入 Sandbox。

---

# 14. 核心模块十：Verification-driven Self-Repair

这是项目最值得在简历里重点写的模块之一。

## 14.1 验证流水线

```text
Code Change
    ↓
Syntax Check
    ↓
Lint
    ↓
Type Check
    ↓
Targeted Test
    ↓
Unit Test
    ↓
Integration Test
    ↓
Reviewer Agent
```

---

## 14.2 Failure Diagnosis

统一错误结构：

```python
class VerificationFailure(BaseModel):
    stage: str
    command: str
    exit_code: int
    stdout: str
    stderr: str
    failed_tests: list[str]
    related_files: list[str]
```

---

## 14.3 Self-Repair Loop

```text
Verification Failed
       │
       ▼
Failure Summarizer
       │
       ▼
Root Cause Analysis
       │
       ▼
Retrieve Related Code
       │
       ▼
Coding Agent Repair
       │
       ▼
Re-run Targeted Test
       │
 ┌─────┴──────┐
 PASS         FAIL
  │             │
  ▼             ▼
Continue     Retry < N
```

限制：

```text
max_repair_attempts = 3
```

避免死循环。

---

# 15. 核心模块十一：Memory

## 15.1 Memory 分类

### Episodic Memory

记录：

```text
过去执行过什么任务
失败原因
修复过程
```

### Semantic Memory

记录：

```text
项目关键概念
业务规则
架构约束
```

### Repository Memory

例如：

```text
"AuthService owns login logic"
"all database access must go through repositories"
"tests use pytest fixtures from conftest.py"
```

### User / Project Rule

例如：

```text
Do not change public APIs.
Use Ruff.
All new code requires unit tests.
```

---

## 15.2 Memory 写入原则

不要记录所有内容。

仅当：

```text
Reusable
Stable
Repository-specific
High-value
```

才持久化。

---

# 16. 核心模块十二：MCP 与外部系统

原底座已有 MCP Client，可以继续保留。

优先接入：

```text
GitHub
Issue Tracker
Documentation
Database Schema
CI System
```

MCP 在本项目中的价值不是为了“展示用了 MCP”，而是：

> 将 Coding Agent Runtime 与企业已有开发工具解耦。

---

# 17. GitHub Issue-to-PR Workflow

后续高级版本：

```text
GitHub Issue
      │
      ▼
RepoPilot
      │
      ▼
Plan
      │
      ▼
Code + Test
      │
      ▼
Review
      │
      ▼
Commit
      │
      ▼
Pull Request
```

PR 内容自动生成：

```text
Summary
Changes
Reason
Tests
Risk
Files Changed
```

---

# 18. Observability

Agent 系统如果没有 Trace，很难 Debug。

每次运行需要生成：

```text
Run
├── Task
│   ├── Agent
│   │   ├── LLM Request
│   │   ├── Tool Call
│   │   ├── Tool Result
│   │   └── Token
│   └── Verification
└── Final Result
```

---

## 18.1 必须记录的指标

```text
LLM calls
Input tokens
Output tokens
Cached tokens
Tool calls
Tool latency
Total runtime
Estimated cost
Files read
Files modified
Tests executed
Repair attempts
Task success / failure
```

---

# 19. 推荐项目目录

```text
repopilot/
│
├── pyproject.toml
├── README.md
├── .env.example
├── config/
│
├── repopilot/
│   │
│   ├── runtime/
│   │   ├── agent.py
│   │   ├── loop.py
│   │   ├── llm.py
│   │   ├── permission.py
│   │   ├── budget.py
│   │   └── events.py
│   │
│   ├── agents/
│   │   ├── planner.py
│   │   ├── explorer.py
│   │   ├── coder.py
│   │   ├── tester.py
│   │   └── reviewer.py
│   │
│   ├── repository/
│   │   ├── scanner.py
│   │   ├── parser.py
│   │   ├── symbols.py
│   │   ├── graph.py
│   │   ├── index.py
│   │   └── incremental.py
│   │
│   ├── retrieval/
│   │   ├── lexical.py
│   │   ├── semantic.py
│   │   ├── structural.py
│   │   ├── hybrid.py
│   │   └── reranker.py
│   │
│   ├── context/
│   │   ├── manager.py
│   │   ├── budget.py
│   │   ├── selector.py
│   │   └── compression.py
│   │
│   ├── orchestration/
│   │   ├── task.py
│   │   ├── dag.py
│   │   ├── planner.py
│   │   ├── scheduler.py
│   │   └── coordinator.py
│   │
│   ├── memory/
│   │   ├── manager.py
│   │   ├── episodic.py
│   │   ├── semantic.py
│   │   └── repository.py
│   │
│   ├── tools/
│   │   ├── registry.py
│   │   ├── file/
│   │   ├── search/
│   │   ├── shell/
│   │   ├── repository/
│   │   ├── git/
│   │   └── test/
│   │
│   ├── workspace/
│   │   ├── worktree.py
│   │   ├── sandbox.py
│   │   └── merge.py
│   │
│   ├── verification/
│   │   ├── pipeline.py
│   │   ├── failure.py
│   │   ├── repair.py
│   │   └── review.py
│   │
│   ├── mcp/
│   │   └── client.py
│   │
│   ├── persistence/
│   │   ├── database.py
│   │   └── models.py
│   │
│   ├── observability/
│   │   ├── trace.py
│   │   ├── metrics.py
│   │   └── cost.py
│   │
│   ├── evaluation/
│   │   ├── runner.py
│   │   ├── swebench.py
│   │   ├── metrics.py
│   │   └── ablation.py
│   │
│   ├── cli/
│   │   ├── main.py
│   │   └── commands.py
│   │
│   └── api/
│       └── app.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── agent_tasks/
│
├── benchmarks/
│
└── docs/
```

---

# 20. 数据模型设计

## 20.1 Run

```python
class AgentRun(BaseModel):
    id: str
    repo: str
    requirement: Requirement
    status: str
    created_at: datetime
    tasks: list[str]
    total_tokens: int = 0
    total_cost: float = 0
```

---

## 20.2 Task

```python
class TaskNode(BaseModel):
    id: str
    title: str
    description: str
    role: str
    dependencies: list[str]
    status: str
    worktree: str | None
    result: str | None
```

---

## 20.3 Tool Event

```python
class ToolEvent(BaseModel):
    agent_id: str
    task_id: str
    tool: str
    arguments: dict
    success: bool
    latency_ms: int
```

---

# 21. CLI 设计

推荐：

```bash
repopilot init
```

索引当前仓库。

```bash
repopilot ask "Explain the authentication flow"
```

只读探索。

```bash
repopilot plan "Add refresh token support"
```

输出 Task DAG。

```bash
repopilot run "Fix issue #123"
```

执行完整自主流程。

```bash
repopilot run --issue 123
```

GitHub Issue 模式。

```bash
repopilot index
```

重新索引。

```bash
repopilot graph TokenService
```

查看符号依赖。

```bash
repopilot benchmark
```

执行 Benchmark。

---

# 22. 开发阶段与实现顺序

以下顺序非常重要。

不要同时实现所有功能。

---

## Phase 0：完整理解并运行原始底座

目标：

- 跑通 Python 版
- 使用至少一个真实 LLM Backend
- 熟悉 Agent Loop
- 熟悉 Tool Dispatch
- 熟悉 Context Compression
- 熟悉 Memory / Skills
- 熟悉 Sub-Agent
- 熟悉 MCP

输出：

```text
docs/original-architecture.md
```

验收：

能够清晰解释：

> User Prompt 到 Tool Result 回到 LLM 的完整调用链。

---

## Phase 1：重构 Agent Runtime

任务：

1. 拆 Tool Registry
2. 抽象 LLM Provider
3. 抽象 AgentConfig
4. 抽象 AgentRuntime
5. 保留原有 Context / Permission / Budget
6. 添加统一 Event
7. 编写 Runtime Unit Tests

验收：

```python
planner = AgentRuntime(...)
explorer = AgentRuntime(...)
```

可以独立运行。

---

## Phase 2：Repository Intelligence MVP

先只支持 Python。

实现：

1. Repo Scanner
2. Tree-sitter Parser
3. Symbol Extraction
4. Import Graph
5. Function/Class Index
6. SQLite Persistence
7. Incremental Update

验收：

针对一个中型 Python Repo：

```text
搜索函数定义
搜索 Class
获取 import dependency
获取函数所在文件
```

准确可用。

---

## Phase 3：Hybrid Retrieval

实现：

1. ripgrep / BM25
2. Code Chunk
3. Embedding Index
4. Structural Expansion
5. Weighted Fusion
6. Context Builder

做一个对比实验：

```text
Grep
Vector
Hybrid
Hybrid + Graph
```

评估：

```text
Recall@K
MRR
Top-K Hit Rate
```

这是简历中第一个可以真正量化的技术点。

---

## Phase 4：Planner + Task DAG

实现：

1. Requirement Schema
2. Planner Prompt
3. TaskNode
4. DAG Validation
5. Scheduler
6. State Machine
7. Retry / Replan

验收：

复杂 Feature 可以输出合法无环 DAG。

---

## Phase 5：Multi-Agent

依次实现：

```text
Explorer
Planner
Coder
Tester
Reviewer
```

不要一开始做十个 Agent。

实现：

- Role Prompt
- Tool ACL
- 独立 Context
- Agent Artifact
- Result Merge

验收：

Planner 能委派任务，Agent 可返回结构化 Artifact。

---

## Phase 6：Git Worktree

实现：

1. Worktree Manager
2. Task Branch
3. Diff Collection
4. Commit
5. Merge
6. Conflict Detection
7. Cleanup

验收：

两个独立 Coding Task 可以同时修改不同 Worktree。

---

## Phase 7：Verification + Self-Repair

实现：

1. Test Detection
2. Command Runner
3. Failure Parser
4. Verification Pipeline
5. Repair Prompt
6. Retry Budget
7. Reviewer

验收：

人为注入一个容易修复的错误：

```text
测试失败
→ Agent 定位
→ 修复
→ 重跑
→ PASS
```

---

## Phase 8：Sandbox + Security

实现：

- Docker Runner
- Resource Limits
- Network Policy
- Timeout
- Secret Filtering
- Permission Rules

---

## Phase 9：Evaluation

先自己构建：

```text
20~50 个 Repository Task
```

之后接：

```text
SWE-bench Lite / Verified
```

必须保存每次运行：

```text
task
model
config
retrieval
result
cost
token
duration
patch
test result
```

---

## Phase 10：产品化

实现：

- CLI 优化
- FastAPI（可选）
- GitHub Issue Connector
- PR Generation
- Trace Viewer（可选）

---

# 23. 测试策略

## 23.1 Unit Test

需要重点覆盖：

```text
Symbol Parser
Graph Builder
Hybrid Ranker
Context Selector
DAG Validator
Scheduler
Tool Permission
Worktree Manager
Failure Parser
```

---

## 23.2 Integration Test

例如：

```text
Requirement
    ↓
Planner
    ↓
Explorer
    ↓
Coder
    ↓
Test
```

使用小型 Fixture Repo。

---

## 23.3 End-to-End Test

创建测试仓库：

```text
sample_bug_repo/
```

预先设计 Bug：

- 错误参数
- 边界条件
- API Bug
- 多文件 Bug

测试 Agent 是否能真正修复。

---

# 24. Benchmark 与实验设计

这是整个项目区别于普通 GitHub Demo 的关键。

## 24.1 基线

### Baseline A

原始 Mini Claude Code：

```text
Agent + Grep + Read + Edit
```

### Baseline B

```text
+ Semantic Retrieval
```

### Baseline C

```text
+ Hybrid Retrieval
```

### Proposed

```text
+ Structural Retrieval
+ Task DAG
+ Multi-Agent
+ Verification
```

---

## 24.2 Evaluation Metrics

### 软件工程任务

```text
Resolve Rate
Pass@1
Patch Apply Rate
Test Pass Rate
```

### Retrieval

```text
Recall@5
Recall@10
MRR
Top-K Hit
```

### Agent Efficiency

```text
Input Tokens
Output Tokens
Tool Calls
Turns
Latency
Cost
Files Read
```

### Repair

```text
Repair Success Rate
Average Repair Attempts
```

---

## 24.3 Ablation Study

推荐做：

```text
Full
- Structural Retrieval
- Semantic Retrieval
- Task DAG
- Reviewer
- Self-Repair
- Repository Memory
```

记录：

```text
Resolve Rate
Token
Cost
Latency
```

这让项目具备“研究 + 工程”双重属性。

---

# 25. 项目关键难点

面试时重点准备以下问题。

## 25.1 大 Repo 的 Context 怎么解决？

不能回答：

> 用 RAG。

应该回答：

> 通过 AST/Symbol 建立仓库结构索引，融合 lexical、semantic、dependency graph 三路召回，并基于 task relevance 与 token budget 动态组装 Context。

---

## 25.2 为什么 Multi-Agent？

不是：

> Agent 越多越强。

而是：

- 隔离不同子任务上下文
- 降低单 Agent 长上下文污染
- 给不同角色不同 Tool Permission
- 并行无依赖任务
- Reviewer 与 Coder 解耦

---

## 25.3 怎么避免 Agent 无限循环？

使用：

```text
max_turns
max_cost
max_task_retry
max_repair_attempt
DAG state
timeout
```

---

## 25.4 如何避免 Agent 修改错代码？

组合：

```text
Repository Retrieval
Dependency Awareness
mtime / diff protection
Worktree
Test
Reviewer
Self-Repair
```

---

## 25.5 为什么需要 Git Worktree？

Multi-Agent 并发修改必须隔离 filesystem state。

Worktree 比简单复制 Repo：

- 成本低
- Git 原生
- Branch 清晰
- 易 Diff
- 易 Merge

---

# 26. 安全要求

Coding Agent 具有实际执行能力，因此需要明确安全边界。

必须设计：

- Tool allow / deny
- Role-based Permission
- Dangerous command detection
- Path traversal protection
- Repository root restriction
- Timeout
- Process termination
- Secret masking
- Environment isolation
- Docker limits
- Human Approval Mode

以下命令默认要求批准或禁止：

```text
rm -rf /
sudo
chmod -R
curl | bash
ssh
scp
docker --privileged
git push --force
```

---

# 27. Performance 优化

## 27.1 并行只读 Tool

保留原始底座并行策略。

例如：

```text
read
grep
symbol_search
semantic_search
```

可以并发。

---

## 27.2 Cache

缓存：

```text
AST
Embedding
Symbol
Repository Summary
LLM Side Query
```

---

## 27.3 Incremental Index

只重新解析 Changed File。

---

## 27.4 Small Model Routing

不同 Agent 使用不同模型。

例如：

```text
Explorer → cheaper model
Planner → strong reasoning model
Coder → strong coding model
Reviewer → strong reasoning model
```

可以进一步形成：

> Dynamic Model Routing

但不建议第一版实现。

---

# 28. MVP 定义

为了保证项目真正落地，MVP 不要包含全部功能。

## MVP 必须完成

```text
Agent Runtime
Repository Scanner
Python AST / Symbol Index
Hybrid Retrieval
Planner
Explorer
Coder
Tester
Task DAG
Git Worktree
Verification
CLI
Basic Trace
```

MVP 暂缓：

```text
Neo4j
Redis
Web UI
Swarm
Kubernetes
多语言
复杂模型路由
```

---

# 29. MVP 验收标准

满足以下条件才认为 MVP 完成：

1. 可以对一个陌生 Python Repo 自动建立索引；
2. 用户输入 Bug / Feature 后能够生成 Task DAG；
3. Explorer 可以定位核心文件和相关 Symbols；
4. Coding Agent 在独立 Worktree 修改代码；
5. Tester 自动执行相关测试；
6. 测试失败可至少进行一次自动 Repair；
7. 最终输出 Git Diff；
8. 所有 Agent 调用、Tool 调用、Token、Cost 可追踪；
9. 至少建立 20 个测试任务；
10. 相比原始 Baseline 给出真实量化实验。

---

# 30. 项目最终 Demo 场景

Demo 不要选：

> “帮我写一个 Hello World。”

建议准备三个场景。

## Demo 1：Bug Fix

```text
Fix refresh token validation when access token is expired.
```

展示：

```text
Retrieve
→ Plan
→ Edit
→ Test Fail
→ Repair
→ Pass
```

---

## Demo 2：跨文件 Feature

```text
Add rate limiting to authentication endpoints.
```

展示：

```text
Dependency Graph
Task DAG
Multi-Agent
Worktree
Tests
```

---

## Demo 3：Codebase Understanding

```text
Explain how authentication flows from API to database.
```

展示：

```text
Symbol
Dependency Graph
Repository Search
```

---

# 31. README 应该展示什么

GitHub 首页必须包含：

1. 项目一句话
2. Architecture Diagram
3. Demo GIF
4. Feature List
5. Why RepoPilot
6. Comparison
7. Quick Start
8. Benchmark
9. Ablation
10. Project Structure
11. Roadmap

一定不要只放：

```text
pip install
python main.py
```

---

# 32. 简历策略

## 32.1 当前尚未完成代码时

如果现在只是已经立项 / 开始开发，简历中必须写成：

> **在研 / 开发中**

不要把尚未完成的模块写成已经实现。

---

## 32.2 当前即可使用的“在研版”简历描述

### 项目名称

**RepoPilot —— 面向大型代码仓库的自主软件工程 Multi-Agent 系统（在研）**

### 技术栈

`Python / LLM Agent / Tree-sitter / RAG / NetworkX / Git Worktree / Docker / MCP / SQLite / SWE-bench`

### 简历描述

**项目背景：** 基于开源 Coding Agent Harness `claude-code-from-scratch` 进行二次开发，面向大型代码仓库中上下文容量受限、跨文件依赖复杂、单智能体任务稳定性不足等问题，设计从自然语言需求 / Issue 到代码定位、任务规划、修改、测试和验证的自主软件工程 Agent。

**核心设计：**

- 规划将原有 Agent Loop、Tool Calling、Context Compression、Memory、Sub-Agent、MCP 等能力重构为通用 Agent Runtime，并通过角色化 Prompt 与 Tool ACL 支持 Planner、Explorer、Coder、Tester、Reviewer 等多智能体协作。
- 设计基于 Tree-sitter 的 Repository Intelligence 模块，对大型代码仓库抽取 AST、Symbol、Import 与调用依赖，构建可增量更新的代码结构索引和依赖图。
- 设计融合关键词/BM25、Embedding 语义召回与 Dependency Graph 结构扩展的 Hybrid Code Retrieval，结合 Token Budget 动态选择高价值代码上下文。
- 设计基于 Task DAG 的任务拆解与调度机制，并规划使用 Git Worktree 隔离不同 Agent 的代码修改环境，降低并行任务间的文件状态冲突。
- 设计 Verification-driven Self-Repair 流程，通过 Lint / Test / Reviewer 反馈驱动 Agent 对失败 Patch 进行诊断、重新检索和自动修复。
- 规划基于 SWE-bench 及自建软件工程任务集，从 Resolve Rate、检索 Recall@K、Token、Tool Calls、Cost 与 Repair Success Rate 等维度对系统进行量化评估和消融实验。

---

# 33. 完成 MVP 后的简历版本模板

> 注意：下面只有在对应模块真正实现后才可以使用。

### RepoPilot —— 面向大型代码仓库的自主软件工程 Multi-Agent 系统

**技术栈：**  
`Python / LLM Agent / Tree-sitter / FAISS(Qdrant) / NetworkX / Git Worktree / Docker / MCP / SQLite(PostgreSQL) / SWE-bench`

**项目描述：**

- 基于 Coding Agent Harness 构建面向大型代码仓库的自主软件工程系统，重构 Agent Loop、Tool Registry、Context、Memory、Permission 与 Budget 等运行时能力，实现 Planner、Explorer、Coder、Tester、Reviewer 多角色 Agent 的独立上下文和工具权限控制。
- 构建 Repository Intelligence 引擎，基于 Tree-sitter 抽取 AST、函数/类 Symbol、Import 与调用关系，并通过增量索引维护代码仓库依赖图，支持符号级定义、引用和跨文件依赖检索。
- 实现 Lexical + Semantic + Structural 三路 Hybrid Code Retrieval，融合 BM25/Grep、Embedding 和 Dependency Graph 多跳扩展，并结合 Context Value Score 与 Token Budget 完成动态代码上下文构建。
- 设计 Task DAG Orchestrator，将复杂软件工程需求拆分为带依赖的子任务，通过 DAG Scheduler 调度多个 Agent，并基于 Git Worktree 为并行 Coding Task 创建隔离开发环境。
- 构建 Verification-driven Self-Repair Loop，自动执行静态检查、Targeted Test 和 Regression Test，根据失败日志进行 Root Cause Analysis、代码重新检索与自动修复，并由 Reviewer Agent 完成最终 Patch 审查。
- 构建 Agent Observability 与 Benchmark Pipeline，跟踪 Token、Cost、Tool Calls、Latency、Files Changed 和 Repair Attempts，并在自建任务集 / SWE-bench 上评估 Resolve Rate、检索 Recall@K 和任务执行效率。

---

# 34. 有实验结果后的强简历模板

未来如果真实跑出了实验，可以将最后两条写成：

```text
- Hybrid Retrieval 相比 Grep Baseline 将 Top-10 相关代码召回率由 XX% 提升至 XX%，同时将平均无效文件读取量降低 XX%。
- 在 XX 个软件工程任务上实现 XX% Resolve Rate；Self-Repair 将首次测试失败任务中的 XX% 自动修复成功，平均每任务 Token/Cost 为 XX。
```

必须填写**真实实验结果**。

绝对不要编造数字。

---

# 35. 面试讲解顺序

面试时不要从：

> “这是一个 Claude Code 仿写项目。”

开始讲。

推荐：

### 第一句

> 我做的是一个面向大型代码仓库的软件工程 Agent，目标是让 Agent 从 Issue 出发自主完成代码定位、任务规划、修改、测试和修复。

### 第二句

> 底层 Agent Harness 是我基于 claude-code-from-scratch 的实现进行理解和重构的，包括 Agent Loop、Tool Calling、Context、Memory 和 Sub-Agent。

### 第三句

> 我主要做的增量集中在大型 Repo 的 Repository Intelligence、Hybrid Retrieval、Task DAG、Multi-Agent Worktree Isolation 以及 Verification-driven Self-Repair。

然后重点展开自己真正实现的模块。

---

# 36. 最值得投入的三个技术点

如果时间有限，只把三个点做深：

## TOP 1：Repository Intelligence + Hybrid Retrieval

这是：

> 大代码仓库 Agent

与：

> 普通 Mini Claude Code

最本质的区别。

---

## TOP 2：Task DAG + Multi-Agent + Worktree

这是：

> Autonomous Software Engineering

的核心。

---

## TOP 3：Verification + Self-Repair + Benchmark

这是把：

> 能生成代码

变成：

> 能证明代码正确。

---

# 37. 项目创新点总结

RepoPilot 相比原始 Mini Claude Code，主要二次开发创新可总结为：

### 创新 1：Repository-aware Agent

从面向当前目录的 Tool Agent，升级为带有仓库全局结构认知的 Agent。

### 创新 2：Structure-aware Hybrid Retrieval

不依赖单一路径 Vector RAG，而是融合：

```text
Lexical
Semantic
AST / Dependency
```

### 创新 3：Task-Graph Multi-Agent Orchestration

从 fork-return Sub-Agent 升级为：

```text
Task DAG + Scheduler + Role Agent
```

### 创新 4：Workspace Isolation

通过：

```text
Git Worktree + Docker
```

支持多个写 Agent 安全并行。

### 创新 5：Verification-driven Autonomous Repair

让 Test / Reviewer 的结果重新驱动：

```text
Retrieve → Reason → Repair → Verify
```

### 创新 6：Evaluation-driven Agent Engineering

通过：

```text
Retrieval Benchmark
Software Engineering Benchmark
Ablation
Cost / Token Evaluation
```

验证各模块真实价值。

---

# 38. 风险与降级方案

## 风险 1：Tree-sitter Call Graph 精度有限

解决：

第一阶段只构建：

```text
Import Graph + Symbol-level lightweight Call Graph
```

后续使用 LSP 增强。

---

## 风险 2：Embedding 成本高

解决：

```text
Incremental Embedding
Local Embedding Model
Cache
```

---

## 风险 3：Multi-Agent 成本过高

解决：

- 小模型用于 Explorer
- Agent Artifact 替代共享完整上下文
- 限制 Agent Turn
- Task Budget
- 并行只读任务

---

## 风险 4：SWE-bench 资源高

解决：

先完成：

```text
20~50 自建任务
```

再扩展到：

```text
SWE-bench Lite / Verified 子集
```

---

## 风险 5：项目范围失控

严格按照：

```text
MVP → Benchmark → Extension
```

不要优先开发 Web UI。

---

# 39. 推荐 Git 提交阶段

建议 Commit History 能体现你独立开发过程：

```text
feat(runtime): refactor reusable agent runtime
feat(repo): add repository scanner and symbol index
feat(repo): build import dependency graph
feat(retrieval): implement lexical code retrieval
feat(retrieval): add semantic embedding index
feat(retrieval): add structural graph expansion
feat(context): add token-aware context selector
feat(planner): implement task DAG
feat(agent): add explorer and coder roles
feat(worktree): isolate coding tasks with git worktree
feat(verify): add test verification pipeline
feat(repair): implement self-repair loop
feat(eval): add retrieval benchmark
feat(eval): add software engineering task runner
```

Git History 本身也是面试时证明项目不是“一次性 AI 生成”的重要证据。

---

# 40. 项目开发完成定义（Definition of Done）

项目不是“能运行”就完成。

至少满足：

### Architecture

- [ ] Agent Runtime 重构完成
- [ ] Multi-Agent Role 完成
- [ ] Tool ACL 完成

### Repository

- [ ] AST Parser
- [ ] Symbol Index
- [ ] Dependency Graph
- [ ] Incremental Update

### Retrieval

- [ ] Lexical
- [ ] Semantic
- [ ] Structural
- [ ] Hybrid Ranker
- [ ] Token-aware Context Builder

### Orchestration

- [ ] Requirement Parser
- [ ] Planner
- [ ] Task DAG
- [ ] Scheduler

### Execution

- [ ] Git Worktree
- [ ] Coding Agent
- [ ] Test Agent
- [ ] Reviewer

### Verification

- [ ] Test Pipeline
- [ ] Failure Parser
- [ ] Self-Repair

### Engineering

- [ ] Persistence
- [ ] Trace
- [ ] Cost
- [ ] Unit Test
- [ ] Integration Test

### Evaluation

- [ ] Retrieval Benchmark
- [ ] Agent Benchmark
- [ ] Baseline
- [ ] Ablation
- [ ] Results Table

### Portfolio

- [ ] Architecture Diagram
- [ ] Demo GIF / Video
- [ ] Complete README
- [ ] Resume Description
- [ ] Interview Notes

---

# 41. 推荐最终项目标题

中文：

> **RepoPilot：面向大型代码仓库的自主软件工程多智能体系统**

英文：

> **RepoPilot: An Autonomous Multi-Agent Software Engineering System for Large-Scale Code Repositories**

也可以更工程化：

> **RepoPilot: Repository-Aware Multi-Agent Coding System**

简历推荐第一个。

---

# 42. 最终项目故事线

整个项目在简历和面试中应该形成非常清晰的一条故事线：

```text
Mini Coding Agent
       │
       │ 问题：大型 Repo 无法仅靠上下文和 Grep 理解
       ▼
Repository Intelligence
       │
       │ 问题：单一召回不稳定
       ▼
Hybrid Retrieval
       │
       │ 问题：复杂需求无法一次完成
       ▼
Task DAG
       │
       │ 问题：单 Agent Context 和能力边界
       ▼
Multi-Agent
       │
       │ 问题：并行修改冲突
       ▼
Git Worktree Isolation
       │
       │ 问题：生成代码不代表正确
       ▼
Verification + Self-Repair
       │
       │ 问题：不知道系统是否真的更好
       ▼
Benchmark + Ablation
```

这条故事线比：

> “我给 Claude Code Clone 加了几个 Agent 和 Tool。”

要强得多。

---

# 43. 开始开发时的优先级

如果现在马上开始写代码：

```text
P0
原项目跑通 + 架构理解
        ↓
P1
Agent Runtime 重构
        ↓
P2
Repository Intelligence
        ↓
P3
Hybrid Retrieval
        ↓
P4
Task DAG
        ↓
P5
Multi-Agent
        ↓
P6
Worktree
        ↓
P7
Verification / Repair
        ↓
P8
Evaluation
```

其中：

> **P2 + P3 是第一阶段最需要自己做深的部分。**

因为这是项目真正从：

> Mini Claude Code

升级为：

> Large Repository Software Engineering Agent

的分界点。

---

# 44. 参考资料

## 原始底座

- `https://github.com/Windy3f3f3f3f/claude-code-from-scratch`

## Claude Code 架构分析姊妹项目

- `https://github.com/Windy3f3f3f3f/how-claude-code-works`

## 后续建议调研主题

在实现具体模块时分别调研：

- Tree-sitter Code Intelligence
- LSP / Symbol Resolution
- Code RAG / Repository-level Retrieval
- SWE-agent
- OpenHands
- Aider Repository Map
- Graph-based Code Retrieval
- SWE-bench
- Git Worktree Agent Isolation
- Agent Context Engineering
- Multi-Agent Software Engineering
- Verification-driven Coding Agent

---

# 45. 最终提醒

**第一，简历先写“在研版”，完成一个模块就将“设计/规划”替换为“实现/构建”。**

**第二，真正决定项目含金量的不是 Agent 数量，而是有没有解决明确的软件工程问题。**

**第三，至少保留一组 Baseline、一个 Benchmark 和一组真实实验结果。**

**第四，优先把 Repository Intelligence、Hybrid Retrieval、Task DAG、Self-Repair 做深，不要优先做漂亮的 Web UI。**

**第五，原项目是底座和学习来源，最终 GitHub README 应明确说明基于其二次开发，并突出 RepoPilot 自己新增的架构与代码。**

---

## 项目最终目标

当项目完成后，你应该能够对面试官用一句话总结：

> **我不是简单复现 Claude Code，而是在自己理解并重构 Coding Agent Harness 的基础上，针对大型代码仓库的上下文检索、复杂任务编排和生成代码验证问题，实现了一个 Repository-aware Multi-Agent Software Engineering Agent，并通过真实 Benchmark 对检索、任务完成率和自动修复效果进行了量化评估。**

