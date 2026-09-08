# RepoPilot

面向大型代码仓库的自主软件工程 Multi-Agent 系统。

基于 [Windy3f3f3f3f/claude-code-from-scratch](https://github.com/Windy3f3f3f3f/claude-code-from-scratch)
Python 版二次开发：保留原始 Agent Loop，在其上按模块扩展出
Runtime、仓库智能、混合检索、任务规划、多代理团队、Git Worktree
隔离、验证与自修复、沙箱安全策略与评测体系。

> 开发过程、每阶段真实执行命令与数字、Git 历史见
> [DEVELOPMENT_RESULTS.md](DEVELOPMENT_RESULTS.md)。
> 本文档的功能状态与代码保持一致（§25 规则：不把计划实现写成已实现）。

## 功能状态（全部对应真实代码）

| 模块 | 状态 | 位置 |
|------|------|------|
| Agent Runtime（配置/注册表/ACL/预算/上下文/事件+Trace） | ✅ | `mini_claude/runtime/` |
| Repository Intelligence（扫描/AST/符号索引/依赖图/增量 SQLite 持久化） | ✅ | `mini_claude/repo/` |
| Hybrid Retrieval（词法 BM25/语义 LSA/结构图/融合重排/上下文构建） | ✅ | `mini_claude/retrieval/` |
| Requirement + Task DAG（解析/七状态机/拓扑调度/重试） | ✅ | `mini_claude/planning/` |
| Multi-Agent（Planner/Explorer/Coder/Tester/Reviewer + Artifact 邮箱） | ✅ | `mini_claude/agents/` |
| Git Worktree Isolation（任务分支/独立工作目录/冲突零覆盖/安全清理） | ✅ | `mini_claude/worktree/` |
| Verification + Self-Repair（八阶段 fail-fast 管道/能力探测/≤3 次修复） | ✅ | `mini_claude/verify/` |
| Docker Sandbox 安全策略（危险命令/密钥过滤/路径守卫/Runner） | ✅ 已接入实际命令执行（shell/tests/lint/验证，三态 auto/on/off，如实降级）；Docker 实机路径在本开发环境未验证（无 docker），见 DEVELOPMENT_RESULTS.md Phase 8/12 | `mini_claude/sandbox/` |
| Evaluation + Benchmark（24 任务套件/四基线/消融/原始数据落盘） | ✅ | `mini_claude/evaluation/`、`tests/benchmark/phase9/` |
| Productization（CLI/GitHub Issue→PR 流/§24 RunLog 可观测性） | ✅ | `mini_claude/product/` |
| FastAPI / Trace Viewer | ❌ 未实现（文档标注为可选项） | — |

## 快速开始

```bash
# 环境（Python ≥3.11，git）
pip install -e python

# 初始化仓库并建立索引
repopilot init
repopilot index

# 查看依赖图 / 制定计划（无需 LLM）
repopilot graph
repopilot plan "把 discount 计算从 order 移到 pricing"

# 提问（检索上下文 + LLM 回答，需要 ANTHROPIC_API_KEY）
repopilot ask "订单总价在哪里计算？"

# 端到端运行一个需求（真实 LLM + 任务 DAG + 并行 worktree + 验证 + 自修复）
export ANTHROPIC_API_KEY=...   # 或 ANTHROPIC_AUTH_TOKEN
repopilot run "修复 multiply() 把加法当乘法的 bug"          # 确定性规划器
repopilot run --llm --jobs 2 "修复 multiply() 把加法当乘法的 bug"   # LLM 规划器 + 并行
repopilot run --sandbox on ...    # 强制 docker 沙箱（无 docker 则前置失败）
# --sandbox auto（默认）：docker 可用时命令在容器内跑，不可用时降级宿主机
# 并在每条结果与 run 汇总中如实标注（0 docker / N host / 0 blocked）

# 评测（真实检索基准，24 任务 × 5 检索栈，原始数据落盘）
repopilot benchmark
```

`repopilot run` 会：解析需求 → 生成 TaskDAG（`--llm` 用 LLM 规划器，
默认确定性规划器）→ 调度器按依赖关系动态派发任务 → 每个 READY 任务
一个独立 git worktree + 一个角色 Agent（`--jobs` 个任务并行）→ 每个
任务真实运行仓库测试套件验证（Agent shell、run_tests/run_lint 与验证
管线全部经 `--sandbox` 三态沙箱执行）→ 失败则自修复（≤3 次）→ 提交 →
按拓扑序合并进集成 worktree（冲突一律中止合并、绝不强写）→ 最终全量
验证 → 生成六节 PR 描述（Summary/Changes/Reason/Tests/Risk/Files
Changed）。GitHub PR 创建通过 `gh` CLI（未安装时给出可直接执行的命令）。
主工作区全程不被切换或写入。

## 测试

```bash
python -m pytest python/tests/ -q      # 当前 481/481
```

覆盖：单元/集成/E2E（E2E = 脚本化 LLM 驱动真实 Agent 循环走完整
worktree→团队→验证→提交→PR 链路）、DAG 执行器（依赖可见性、真实
并行 wall-clock、合并冲突不覆盖、失败级联 BLOCKED、修复闭环）、任务
套件完整性（24/24 bug 态失败 + 修复态通过）、真实 LLM 验收记录（见
DEVELOPMENT_RESULTS.md）。

## 真实评测数字（来源：仓库内原始数据文件）

检索（24 任务 × 5 栈，`tests/benchmark/phase9/retrieval_results.json`）：

| 栈 | MRR |
|----|-----|
| grep（Baseline A） | 0.701 |
| semantic（B） | 0.722 |
| hybrid（C）/ Full | 0.917 |
| -Semantic 消融 | 0.896 |

代理（14 个真实 LLM run，`tests/benchmark/phase9/agentic_results/`）：
A 6/6 resolve（$0.110），Proposed 6/6（$0.766，输入 tokens 为 A 的
6.4 倍），-Reviewer 2/2（$0.165）。任务为小型模板仓库，数字不代表
大仓库结论——原始逐 run 数据可查。

## 架构

```
Requirement ─► Planner ─► TaskDAG ─► DagRunner ─► 每任务一个 Worktree
     │                                      │        （Scheduler 动态派发，
     └── HybridRetriever(词法+语义+结构) ◄──┘         独立任务并行线程）
                                                    │
        VerificationPipeline(8 阶段) ◄── 变更 ──────┘（每任务验证 + 自修复）
                │ 失败                                  │
        SelfRepairEngine(≤3 次) ──► 复验                │ 拓扑序合并进集成
                │                                     worktree（冲突绝不强写）
        Commit + 最终全量验证 + PR 描述 + §24 RunLog(JSONL)
```

## 诚实声明

- 沙箱 Docker 实机执行在本开发环境未验证（无 docker daemon）；argv
  翻译、三闸门（deny 列表/密钥过滤/路径限制）、三态降级与如实记录
  均有注入 executor 的单测 + 真实 CLI 证据（Phase 12）。
- 工具探测探针（--version）在宿主机执行；阶段命令才是沙箱化的执行体。
- `repopilot run` 已接入 TaskDAG 调度器（Phase 11）；Phase 9 基准中
  Proposed 栈当时按 TeamRunner 组合执行，故 -TaskDAG 消融在**该基准
  数据内**仍记录为 equivalent-by-construction（数据按当时执行如实
  保存，不追溯改写）。
- 跨任务仓库记忆仍未接入（-RepositoryMemory 消融同前）。
- 所有 Benchmark 数字来自仓库内原始结果文件，禁止伪造。

License: MIT（上游 claude-code-from-scratch 同协议）
