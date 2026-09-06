# RepoPilot Claude Code 全自动二次开发执行规约

## 1. 你的角色

你是本项目 RepoPilot 的主要软件工程开发者。

本项目基于：

`Windy3f3f3f3f/claude-code-from-scratch`

的 Python 版本进行二次开发。

你的任务不是重新实现一个 Claude Code Clone，也不是简单增加 Tool，而是在充分理解现有代码的基础上，将其逐步重构和扩展为：

**RepoPilot：面向大型代码仓库的自主软件工程 Multi-Agent 系统。**

最终目标是实现：

```text
Requirement / GitHub Issue
        ↓
Repository Understanding
        ↓
Hybrid Code Retrieval
        ↓
Planner
        ↓
Task DAG
        ↓
Multi-Agent Execution
        ↓
Git Worktree Isolation
        ↓
Coding
        ↓
Testing
        ↓
Review
        ↓
Self-Repair
        ↓
Patch / Commit / Pull Request
```

整个项目由你按照本文档规定的开发阶段自主推进。

---

# 2. 最重要的开发原则

## 2.1 禁止一次性重写项目

必须先理解原项目，再进行渐进式重构。

必须最大程度复用原有：

- Agent Loop
- Tool Calling
- Context Compression
- Permission
- Budget
- Session
- Memory
- Skills
- Sub-Agent
- MCP
- CLI

不得为了“架构更漂亮”而无理由推翻已经工作的代码。

---

## 2.2 一次只开发一个一级模块

禁止同时实现多个 Phase。

严格按照：

```text
Phase 0
↓
Phase 1
↓
Phase 2
↓
Phase 3
↓
...
```

顺序推进。

前一个 Phase：

```text
实现完成
+
测试完成
+
验证通过
+
结果文档更新
+
Git Commit
+
GitHub Push
+
集成验证通过
```

之后，才能开始下一个 Phase。

---

# 3. Git 分支管理规则

整个开发过程中必须保留清晰、真实、可追踪的 Git History。

禁止所有代码长期堆积在一个分支开发。

---

## 3.1 分支结构

保留原始默认分支：

```text
main
```

不要直接在 `main` 上进行 RepoPilot 开发。

创建长期集成分支：

```text
repopilot-dev
```

所有开发模块均基于当前最新的：

```text
repopilot-dev
```

创建模块分支。

例如：

```text
main
│
└── repopilot-dev
      │
      ├── chore/phase-00-baseline
      │
      ├── feat/phase-01-agent-runtime
      │
      ├── feat/phase-02-repository-intelligence
      │
      ├── feat/phase-03-hybrid-retrieval
      │
      ├── feat/phase-04-task-dag
      │
      ├── feat/phase-05-multi-agent
      │
      ├── feat/phase-06-worktree
      │
      ├── feat/phase-07-verification-repair
      │
      ├── feat/phase-08-sandbox-security
      │
      ├── feat/phase-09-evaluation
      │
      └── feat/phase-10-productization
```

---

# 4. 每个模块必须执行的标准工作流

以下流程是强制要求。

每开发一个 Phase，都必须完整执行。

---

## Step 1：同步开发基线

开始 Phase 前：

```bash
git status
git fetch origin
git checkout repopilot-dev
git pull --ff-only origin repopilot-dev
```

确认：

```text
working tree clean
```

如果存在未知未提交代码：

禁止直接删除。

先分析代码来源和用途。

---

## Step 2：建立独立模块分支

例如开发 Repository Intelligence：

```bash
git checkout -b feat/phase-02-repository-intelligence
```

之后该 Phase 的所有代码、测试和文档修改均在该分支完成。

---

## Step 3：分析当前代码

禁止立刻开始写代码。

首先检查：

```text
现有目录结构
现有模块职责
现有测试
现有配置
当前依赖
可复用代码
当前 API
与前一 Phase 的接口
```

然后确定：

```text
需要新增哪些文件
需要修改哪些文件
哪些旧代码需要重构
哪些接口必须保持兼容
```

---

## Step 4：实现当前模块

只实现当前 Phase 定义的功能。

禁止提前大量实现后续 Phase。

如果当前 Phase 为：

```text
Repository Intelligence
```

则不要同时实现：

```text
Multi-Agent
Self-Repair
GitHub PR
Web UI
```

---

# 5. 强制测试原则

任何模块都不允许：

```text
代码写完 = 模块完成
```

正确标准是：

```text
Implementation
      ↓
Unit Test
      ↓
Static Verification
      ↓
Targeted Test
      ↓
Regression Test
      ↓
Integration Test
      ↓
PASS
```

---

# 6. 测试失败处理规则

如果测试失败：

禁止忽略错误继续下一 Phase。

进入：

```text
FAIL
 ↓
收集 stderr/stdout
 ↓
分析 Root Cause
 ↓
定位相关代码
 ↓
最小化修改
 ↓
重新执行 Targeted Test
 ↓
Regression Test
```

默认：

```text
max_repair_attempts = 3
```

如果三次修复后仍然失败：

将 Phase 标记为：

```text
BLOCKED
```

并在结果文件中记录：

```text
失败测试
错误日志
已经尝试的修复
可能原因
未解决问题
当前分支
当前 Commit
```

禁止伪造 PASS。

---

# 7. 每个 Phase 完成后的 Git 流程

只有当前 Phase 验证通过后才允许提交。

首先检查：

```bash
git status
git diff
git diff --stat
```

确认没有：

```text
临时文件
API Key
Token
.env
缓存
测试产物
无关文件
意外大文件
```

然后 Commit。

Commit 必须采用 Conventional Commit。

例如：

```text
feat(runtime): refactor reusable agent runtime

feat(repo): add repository scanner and symbol index

feat(repo): build import dependency graph

feat(retrieval): implement lexical semantic structural retrieval

feat(planner): implement task DAG scheduler

feat(agent): add multi-agent role runtime

feat(worktree): isolate coding tasks with git worktree

feat(verify): add verification pipeline

feat(repair): implement self-repair loop

feat(eval): add repository task benchmark
```

一个 Phase 可以包含多个逻辑清晰的 Commit。

不要把整个 Phase 压成一个巨型 Commit。

---

# 8. Push 规则

模块验证完成后：

必须将模块分支上传 GitHub。

例如：

```bash
git push -u origin feat/phase-02-repository-intelligence
```

Push 后验证：

```bash
git branch -vv
git log --oneline -n 10
```

确认远程 Tracking Branch 已建立。

如果 Push 失败：

必须分析：

```text
remote
authentication
permission
branch protection
network
```

不得将：

```text
GitHub Push 成功
```

写入结果文件，除非实际成功。

如果 GitHub Push 是硬性步骤且无法完成，则 Phase 状态必须记录为：

```text
IMPLEMENTED_BUT_PUSH_BLOCKED
```

---

# 9. 集成到 repopilot-dev

Phase Branch Push 成功以后：

切回：

```bash
git checkout repopilot-dev
git pull --ff-only origin repopilot-dev
```

使用：

```bash
git merge --no-ff --no-commit <phase-branch>
```

暂时不要立即创建 Merge Commit。

首先重新执行：

```text
核心 Unit Tests
Integration Tests
Regression Tests
```

如果 PASS：

创建 Merge Commit：

```bash
git commit
git push origin repopilot-dev
```

如果 FAIL：

```bash
git merge --abort
```

返回 Phase Branch 修复。

禁止把测试失败的集成状态 Push 到：

```text
repopilot-dev
```

---

# 10. main 分支保护

整个自动开发阶段：

禁止直接：

```bash
git push origin main
```

禁止：

```bash
git push --force
```

禁止修改远程历史。

全部开发完成后：

可以生成：

```text
repopilot-dev → main
```

的 Pull Request。

但不要自动强制合并 main。

---

# 11. 强制开发结果文件

项目根目录必须维护：

```text
DEVELOPMENT_RESULTS.md
```

该文件是整个 RepoPilot 二次开发过程的真实工程记录。

它不是 README。

README 面向项目使用者。

`DEVELOPMENT_RESULTS.md` 面向：

```text
开发过程
模块实现记录
实验结果
验证证据
Git History
问题追踪
```

---

# 12. DEVELOPMENT_RESULTS.md 固定结构

文件顶部维护：

```markdown
# RepoPilot Development Results

## Project Status

Current Phase:
Overall Status:
Integration Branch: repopilot-dev
Last Updated:

| Phase | Module | Branch | Status | Tests | Remote Push |
|------|------|------|------|------|------|
```

每完成一个 Phase，都必须追加完整记录。

---

## Phase X：模块名称

### 1. 开发目标

说明：

```text
为什么开发这个模块
该模块解决什么问题
与前后模块是什么关系
```

---

### 2. 实现内容

必须具体描述：

```text
实现了什么
用了什么
解决了什么
```

禁止只写：

```text
完成 Repository Intelligence。
```

应该类似：

```text
使用 Tree-sitter 对 Python 文件进行 AST 解析，
实现函数、类、方法和 Import Symbol 抽取，
并将 Symbol Metadata 持久化至 SQLite，
为后续 Hybrid Retrieval 提供结构化代码索引。
```

---

### 3. 新增文件

记录：

```text
path/to/file.py
path/to/test.py
...
```

并说明每个关键文件作用。

---

### 4. 修改文件

记录：

```text
path/to/existing.py
```

说明：

```text
为什么修改
修改了什么
是否影响现有接口
```

---

### 5. 核心设计

记录当前模块主要：

```text
Class
Interface
Data Model
Algorithm
Data Flow
```

---

### 6. 测试

记录真实执行的命令。

例如：

```bash
pytest tests/unit/repository -v
pytest tests/integration -v
ruff check .
```

---

### 7. 验证结果

记录：

```text
PASS / FAIL
```

以及：

```text
Passed:
Failed:
Skipped:
Execution time:
```

如果有 Benchmark：

必须保存真实数字。

---

### 8. Self-Repair

如果开发过程中测试失败，记录：

```text
失败原因
错误表现
Root Cause
修改方案
重新测试结果
Repair Attempts
```

如果没有：

```text
Repair attempts: 0
```

---

### 9. Git 信息

记录：

```text
Branch:
Commits:
Remote:
Push Status:
Integration Status:
```

例如：

```text
Branch:
feat/phase-02-repository-intelligence

Commits:
a13cd21 feat(repo): add repository scanner
cb8432a feat(repo): add tree-sitter symbol parser
99f351a test(repo): add repository intelligence tests

Remote:
origin/feat/phase-02-repository-intelligence

Push Status:
PASS

Integration:
Merged into repopilot-dev
```

---

### 10. 当前模块最终实现能力

用 3～6 条简洁说明总结。

必须采用：

```text
用了什么 → 实现了什么
```

的形式。

例如：

```text
1. 使用 Tree-sitter，实现 Python 函数、类、方法和 Import 的 AST 解析与 Symbol 抽取。

2. 使用 NetworkX，实现 File/Module/Symbol 间 Import 与 Dependency Graph 构建。

3. 使用 SQLite，实现 Repository Metadata 和 Symbol Index 的持久化。

4. 使用文件 Hash 与 Git Diff，实现 Changed File 的增量重新索引。
```

---

### 11. 已知问题

记录当前真正存在的问题。

没有则写：

```text
None
```

禁止隐藏问题。

---

### 12. 下一阶段依赖

说明：

```text
下一 Phase 可以复用什么
哪些接口已经稳定
还有哪些限制
```

---

# 13. Phase 0：理解原始项目

Branch：

```text
chore/phase-00-baseline
```

目标：

完整运行并理解原始 Python 版本。

必须分析：

```text
Agent Loop
LLM Backend
Tool Dispatch
Tool Calling
Streaming
Context Compression
Permission
Budget
Session
Memory
Skills
Sub-Agent
MCP
CLI
```

重点梳理完整调用链：

```text
User Input
→ Agent Loop
→ LLM
→ Tool Call
→ Tool Dispatcher
→ Tool Execution
→ Tool Result
→ Context
→ LLM
→ Final Response
```

输出：

```text
docs/original-architecture.md
```

必须实际运行现有测试和至少一次有效 Agent Flow。

验收标准：

能够从代码层面解释：

```text
User Prompt 到 Tool Result 再返回 LLM 的全过程。
```

验证通过后：

```text
Commit
Push phase branch
Merge repopilot-dev
Regression Test
Push repopilot-dev
```

---

# 14. Phase 1：Agent Runtime 重构

Branch：

```text
feat/phase-01-agent-runtime
```

实现：

```text
Tool Registry
Tool Base Interface
LLMProvider
AgentConfig
AgentRuntime
Permission / Tool ACL 基础
Budget
Context 接口
统一 Event
基础 Trace
```

必须尽量复用原始 Agent Loop。

目标是将：

```text
单一 CLI Agent
```

重构为：

```text
可实例化多个角色的通用 Agent Runtime
```

必须支持：

```python
planner = AgentRuntime(...)
explorer = AgentRuntime(...)
```

分别独立运行。

必须增加 Runtime Unit Tests。

重点验证：

```text
Tool registration
Tool dispatch
Read-only permission
AgentConfig validation
Budget
Event emission
LLM mock execution
```

---

# 15. Phase 2：Repository Intelligence

Branch：

```text
feat/phase-02-repository-intelligence
```

这是项目第一核心技术模块。

第一阶段仅支持：

```text
Python Repository
```

实现：

```text
Repository Scanner
Tree-sitter Python Parser
Symbol Extraction
Function Index
Class Index
Method Index
Import Extraction
Dependency Graph
SQLite Persistence
Incremental Index
```

Repository Scanner 自动忽略：

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
generated files
large binaries
```

必须能够：

```text
find_symbol()
find_definition()
获取 symbol 所在文件
获取 import dependency
获取模块依赖关系
```

建立专门 Fixture Repository。

必须测试：

```text
function extraction
class extraction
method extraction
imports
nested symbols
syntax edge cases
dependency graph
incremental update
ignored directories
```

验收：

对中型 Python Repository 能可靠执行：

```text
搜索函数
搜索 Class
定位定义
查看 Import Dependency
定位文件
```

---

# 16. Phase 3：Hybrid Retrieval + Context

Branch：

```text
feat/phase-03-hybrid-retrieval
```

这是项目第二核心技术模块。

实现：

```text
Lexical Retrieval
Semantic Retrieval
Structural Retrieval
Structural Expansion
Candidate Merge
Weighted Fusion
Reranker
Context Builder
Token-aware Context Selection
```

整体路线：

```text
Requirement
     ↓
Query Analyzer
     ↓
┌────────┬──────────┬────────────┐
Lexical   Semantic   Structural
└────────┴──────────┴────────────┘
     ↓
Candidate Merge
     ↓
Rerank
     ↓
Context Builder
```

Structural Retrieval 必须利用 Phase 2 的：

```text
Symbol Index
Dependency Graph
```

支持：

```text
1-hop
2-hop
```

结构扩展。

必须建立 Retrieval Benchmark。

比较：

```text
Grep
Semantic
Hybrid
Hybrid + Graph
```

至少计算：

```text
Recall@5
Recall@10
MRR
Top-K Hit Rate
```

所有数字必须由真实实验产生。

禁止编造 Benchmark。

---

# 17. Phase 4：Requirement Understanding + Task DAG

Branch：

```text
feat/phase-04-task-dag
```

实现：

```text
Requirement Schema
Requirement Parser
Planner Prompt
TaskNode
DAG
DAG Validator
Scheduler
State Machine
Retry
Replan
```

支持：

```text
Bug
Feature
Refactor
Test
Documentation
GitHub Issue-like Requirement
Stack Trace
Test Failure
```

Task 必须包含：

```text
id
title
description
agent_role
dependencies
status
priority
files
budget
```

必须检测：

```text
cycle
missing dependency
duplicate task id
invalid transition
```

Scheduler 只能执行：

```text
所有 dependencies 已完成
```

的任务。

验收：

一个复杂 Feature 可以生成合法的：

```text
Directed Acyclic Graph
```

---

# 18. Phase 5：Multi-Agent

Branch：

```text
feat/phase-05-multi-agent
```

只实现五个核心 Agent：

```text
Planner
Explorer
Coder
Tester
Reviewer
```

不要增加十几个无意义角色。

每个 Agent 必须：

```text
独立 Prompt
独立 Tool ACL
独立 Context
独立 Budget
结构化输出
```

角色权限：

Planner：

```text
read-only
```

Explorer：

```text
read
grep
glob
symbol search
dependency search
semantic search
git log
```

禁止修改代码。

Coder：

```text
read
search
edit
write
bash
git diff
```

Tester：

```text
read
test
lint
build
failure parsing
```

Reviewer：

```text
read-only
diff
test results
dependency inspection
```

Agent 之间禁止无限自由聊天。

必须通过：

```text
AgentArtifact
```

交换结构化信息。

测试：

```text
ACL enforcement
Planner delegation
Artifact serialization
Independent context
Coder write permission
Explorer write rejection
Reviewer write rejection
```

验收：

```text
Requirement
→ Planner
→ Explorer
→ Coder
→ Tester
→ Reviewer
```

最小流程能够跑通。

---

# 19. Phase 6：Git Worktree Isolation

Branch：

```text
feat/phase-06-worktree
```

实现：

```text
WorktreeManager
Task Branch
Worktree Creation
Diff Collection
Commit
Merge
Conflict Detection
Cleanup
```

Coding Task 必须能够创建：

```text
独立 Branch
+
独立 Worktree
+
独立 Working Directory
+
独立 Git Diff
```

例如：

```text
worktrees/
├── task-T001/
├── task-T002/
└── task-T003/
```

必须测试：

两个无依赖 Coding Task：

```text
同时创建两个 Worktree
修改不同文件
互不污染 filesystem state
产生独立 Git Diff
```

还必须测试：

```text
merge conflict detection
dirty workspace
invalid branch
cleanup
```

禁止冲突时暴力覆盖代码。

---

# 20. Phase 7：Verification + Self-Repair

Branch：

```text
feat/phase-07-verification-repair
```

这是第三个重点技术模块。

建立 Verification Pipeline：

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
Regression Test
    ↓
Reviewer
```

实际命令根据 Repository 能力自动检测。

不要假设所有项目都有完全相同的：

```text
ruff
mypy
pytest
```

但必须记录最终选择了哪些验证工具。

建立统一：

```text
VerificationFailure
```

包含：

```text
stage
command
exit_code
stdout
stderr
failed_tests
related_files
```

Self-Repair：

```text
Verification Failure
      ↓
Failure Summarizer
      ↓
Root Cause Analysis
      ↓
Retrieve Related Code
      ↓
Coder Repair
      ↓
Targeted Re-test
```

限制：

```text
max_repair_attempts = 3
```

必须设计一个：

```text
sample_bug_repo
```

人为注入一个容易修复的错误。

必须真实演示：

```text
FAIL
→ Diagnose
→ Repair
→ Re-test
→ PASS
```

并将完整过程记录到：

```text
DEVELOPMENT_RESULTS.md
```

---

# 21. Phase 8：Docker Sandbox + Security

Branch：

```text
feat/phase-08-sandbox-security
```

实现：

```text
Docker Runner
CPU Limit
Memory Limit
Timeout
Disk/Workspace Restriction
Network Policy
Environment Filtering
Secret Filtering
Permission Rules
Dangerous Command Detection
Path Traversal Protection
Repository Root Restriction
```

默认禁止或要求人工批准：

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

严禁向 Sandbox 默认注入：

```text
SSH Key
Cloud Credential
Personal Token
Host Docker Socket
```

必须为安全策略编写 Unit Tests。

---

# 22. Phase 9：Evaluation + Benchmark

Branch：

```text
feat/phase-09-evaluation
```

首先构建：

```text
20～50 个 Repository Task
```

包含：

```text
Bug Fix
Feature
Cross-file Change
API Bug
Boundary Condition
Repository Understanding
```

建立 Baselines：

```text
Baseline A:
Original Mini Coding Agent
+ Grep
+ Read
+ Edit

Baseline B:
+ Semantic Retrieval

Baseline C:
+ Hybrid Retrieval

Proposed:
+ Structural Retrieval
+ Task DAG
+ Multi-Agent
+ Verification
```

至少统计：

软件工程：

```text
Resolve Rate
Pass@1
Patch Apply Rate
Test Pass Rate
```

Retrieval：

```text
Recall@5
Recall@10
MRR
Top-K Hit
```

效率：

```text
Input Tokens
Output Tokens
Tool Calls
Turns
Latency
Cost
Files Read
Files Modified
```

Self-Repair：

```text
Repair Success Rate
Average Repair Attempts
```

还必须进行 Ablation：

```text
Full
- Structural Retrieval
- Semantic Retrieval
- Task DAG
- Reviewer
- Self-Repair
- Repository Memory
```

必须保存原始实验结果。

禁止只在 README 中写最终数字而不保存实验数据。

---

# 23. Phase 10：Productization

Branch：

```text
feat/phase-10-productization
```

完善 CLI：

```bash
repopilot init
repopilot index
repopilot ask
repopilot plan
repopilot run
repopilot graph
repopilot benchmark
```

根据现有 MCP 能力逐步增加：

```text
GitHub Issue Input
Issue → Requirement
Requirement → RepoPilot Run
Patch
Commit
PR Description Generation
Pull Request
```

PR 内容至少包含：

```text
Summary
Changes
Reason
Tests
Risk
Files Changed
```

FastAPI 与 Trace Viewer 属于可选项。

不要因为产品化而延迟核心 MVP。

---

# 24. Observability 要求

每次 Agent Run 至少记录：

```text
Run
Task
Agent
LLM Request
Tool Call
Tool Result
Verification
Repair
Final Result
```

指标包括：

```text
LLM Calls
Input Tokens
Output Tokens
Cached Tokens
Tool Calls
Tool Latency
Runtime
Estimated Cost
Files Read
Files Modified
Tests Executed
Repair Attempts
Task Success
Task Failure
```

这些数据后续直接用于 Benchmark。

---

# 25. README 更新规则

每完成一个真正可用的模块，可以更新 README。

但是：

禁止把：

```text
计划实现
```

写成：

```text
已经实现
```

README 中功能状态必须与代码一致。

Benchmark 数字必须来自真实：

```text
results
csv
json
database
trace
```

不得生成虚假实验结果。

---

# 26. Claude Code 自主执行行为

正常情况下，不要因为小问题频繁询问用户。

优先：

```text
读取代码
分析
执行测试
查看错误
修复
重新测试
继续
```

以下情况才应停止自动推进并明确报告：

```text
GitHub Authentication 完全不可用
关键 Credential 缺失
存在无法安全处理的 destructive operation
需求与现有代码存在不可调和冲突
连续 Self-Repair 达到最大次数仍失败
现有 Repository 本身已经处于损坏状态
```

不得：

```text
假装运行了测试
假装 Push 成功
假装 Benchmark 完成
假装模块实现完成
```

---

# 27. Phase Completion Gate

一个 Phase 只有同时满足以下条件才能标记：

```text
COMPLETED
```

条件：

```text
[ ] 当前 Phase 功能全部实现
[ ] 新增必要 Unit Tests
[ ] Unit Tests PASS
[ ] Targeted Tests PASS
[ ] Integration Tests PASS
[ ] Existing Regression Tests PASS
[ ] Git Diff 已人工式检查
[ ] 无 Secret/Token 被提交
[ ] DEVELOPMENT_RESULTS.md 已更新
[ ] Phase Branch Commit 完成
[ ] Phase Branch 已 Push GitHub
[ ] Phase Branch 成功集成 repopilot-dev
[ ] Integration Regression Test PASS
[ ] repopilot-dev 已 Push GitHub
```

缺少任何一个：

不得标记 COMPLETED。

---

# 28. 全项目最终 Definition of Done

最终至少必须完成：

```text
Agent Runtime
Tool Registry
Tool ACL

Repository Scanner
AST Parser
Symbol Index
Dependency Graph
Incremental Index

Lexical Retrieval
Semantic Retrieval
Structural Retrieval
Hybrid Ranker
Token-aware Context Builder

Requirement Parser
Planner
Task DAG
Scheduler

Explorer Agent
Coder Agent
Tester Agent
Reviewer Agent

Git Worktree

Verification Pipeline
Failure Parser
Self-Repair

Persistence
Trace
Cost Metrics

Unit Tests
Integration Tests
End-to-End Tests

Retrieval Benchmark
Agent Benchmark
Baseline Comparison
Ablation Study
Results

Complete README
DEVELOPMENT_RESULTS.md
```

只有真实实现的模块才允许出现在最终项目能力描述中。

---

# 29. 最终 Git 输出

项目开发结束后，GitHub 应能够看到清晰的模块开发历史，例如：

```text
chore/phase-00-baseline
feat/phase-01-agent-runtime
feat/phase-02-repository-intelligence
feat/phase-03-hybrid-retrieval
feat/phase-04-task-dag
feat/phase-05-multi-agent
feat/phase-06-worktree
feat/phase-07-verification-repair
feat/phase-08-sandbox-security
feat/phase-09-evaluation
feat/phase-10-productization
```

每个分支都必须有：

```text
代码
测试
Commit
Remote Branch
开发结果记录
```

最终：

```text
repopilot-dev
```

应该完整包含 RepoPilot 的当前稳定开发版本。

---

# 30. 现在开始执行

现在不要直接开始 Phase 1。

首先执行 Phase 0。

第一步：

```text
1. 查看完整 Repository 文件树
2. 查看 Git 状态与 Remote
3. 确认默认分支
4. 查看 pyproject.toml / requirements
5. 查看原始测试
6. 运行原始项目测试
7. 阅读 Agent Loop
8. 阅读 Tool Dispatch
9. 阅读 Context / Memory / Skills / Sub-Agent / MCP
10. 梳理完整调用链
11. 创建 docs/original-architecture.md
12. 创建 DEVELOPMENT_RESULTS.md
13. 创建 chore/phase-00-baseline
14. 完成 Phase 0 验证
15. Commit
16. Push GitHub
17. 集成 repopilot-dev
18. 验证
19. 更新 DEVELOPMENT_RESULTS.md
20. 再开始 Phase 1
```

之后严格按照本文档持续推进，直到 RepoPilot MVP 和后续 Evaluation 完成。