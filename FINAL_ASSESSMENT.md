# RepoPilot 全量收尾评估

> 生成日期：2026-09-12（Phase 17 完成后）
> 数据来源：真实代码、真实命令输出、真实测试运行、git 历史。
> 本文件是最终评估快照；持续开发记录见 DEVELOPMENT_RESULTS.md。

## 1. 结论

需求文档（"RepoPilot Claude Code 全自动二次开发执行规约"）定义的
RepoPilot 已全部实现：Phase 0-10 完成 MVP，用户评审的 **8 项差距
全部修复**（Phase 11-17 + fix/plan-llm-cli），最终 Definition of Done
（§28）逐项核对通过，§29 要求的 Git 输出完整。最终测试 **545/545
通过**（451.55s，真实运行）。

## 2. 最终 Definition of Done（§28）逐项核对

| §28 要求 | 状态 | 位置 | 证据 |
|---|---|---|---|
| Agent Runtime | ✅ | `mini_claude/runtime/` | Phase 1；59 测试 |
| Tool Registry | ✅ | `mini_claude/runtime/`（build_default_registry） | Phase 1 |
| Tool ACL | ✅ | `mini_claude/agents/`（check_permission 内核） | Phase 1/5；32 测试 |
| Repository Scanner | ✅ | `mini_claude/repo/scanner.py` | Phase 2/13 |
| AST Parser | ✅ | `repo/parser.py` + tree-sitter py/js/ts(tsx) | Phase 2/13；90 测试 |
| Symbol Index | ✅ | `repo/index.py` | Phase 2 |
| Dependency Graph | ✅ | `repo/graph.py` | Phase 2 |
| Incremental Index | ✅ | mtime/size+sha256 增量 + `load_or_build()` | Phase 2/16 |
| Lexical Retrieval | ✅ | `retrieval/lexical.py`（BM25+中文） | Phase 3/15 |
| Semantic Retrieval | ✅ | `retrieval/semantic.py`（神经模型默认，LSA 诚实回退） | Phase 3/14 |
| Structural Retrieval | ✅ | `retrieval/structural.py`（图扩展） | Phase 3 |
| Hybrid Ranker | ✅ | `retrieval/fusion.py`（RRF+重排） | Phase 3 |
| Token-aware Context Builder | ✅ | `retrieval/context.py` | Phase 3 |
| Requirement Parser | ✅ | `planning/`（RequirementParser） | Phase 4 |
| Planner | ✅ | 确定性 + LLM 规划器 | Phase 4/11 |
| Task DAG | ✅ | `planning/dag.py`（七状态机） | Phase 4 |
| Scheduler | ✅ | `planning/dag.py` + `execution/runner.py` 动态调度 | Phase 4/11 |
| Explorer/Coder/Tester/Reviewer Agent | ✅ | `agents/roles.py`（+planner，五角色） | Phase 5 |
| Git Worktree | ✅ | `worktree/manager.py`（冲突零覆盖/安全清理） | Phase 6/17 |
| Verification Pipeline | ✅ | `verify/pipeline.py`（八阶段 fail-fast） | Phase 7 |
| Failure Parser | ✅ | `verify/`（pytest 输出解析） | Phase 7 |
| Self-Repair | ✅ | `verify/repair.py`（≤3 次有界修复） | Phase 7 |
| Persistence | ✅ | `repo/store.py`（SQLite）+ index.db 复用 + 向量缓存 + runlog | Phase 2/14/16 |
| Trace | ✅ | runtime 事件 + Trace | Phase 1 |
| Cost Metrics | ✅ | trace metrics cost_usd（真实 run 逐条记录 $） | Phase 1/5 |
| Unit Tests | ✅ | 545 项（tests/ 各目录） | 全 Phase |
| Integration Tests | ✅ | 同上（跨模块集成用例） | 全 Phase |
| End-to-End Tests | ✅ | 脚本化 LLM 驱动真实 Agent 循环（agents/execution/product） | Phase 5+ |
| Retrieval Benchmark | ✅ | 24 任务 × 5 检索栈，原始 JSON 落盘 | Phase 9 |
| Agent Benchmark | ✅ | agentic 评测（真实 LLM，基线 A/B/C + Proposed） | Phase 9 |
| Baseline Comparison | ✅ | Grep/Semantic/Hybrid/Hybrid+Graph 四基线 | Phase 9 |
| Ablation Study | ✅ | -Structural/-Semantic/-TaskDAG/-Reviewer/-Self-Repair/-Memory | Phase 9 |
| Results | ✅ | `tests/benchmark/phase9/retrieval_results.json` + agentic_results | Phase 9 |
| Complete README | ✅ | README.md 功能表与代码一致（本次核对） | §25 规则 |
| DEVELOPMENT_RESULTS.md | ✅ | Phase 0-17 完整记录（2911 行） | §11/§12 结构 |

可选未实现（文档明示可选项，如实保持 ❌）：FastAPI、Trace Viewer。

## 3. Git 输出核对（§29）

远端 21 个分支全部存在，每个 Phase 分支含代码/测试/Commit/记录：

```
chore/phase-00-baseline · feat/phase-01 … phase-17（17 个 Phase 分支）
fix/plan-llm-cli · main（仅 baseline，从未被直接推送）· repopilot-dev（稳定开发版）
```

- `main..repopilot-dev`：112 commits，165 files，+30,992 行。
- 全部 Phase 经 merge 进入 repopilot-dev（含 commit-tree 合并方式处理的
  用户工作区冲突），无 force-push，main 从未被直接推送（§10 遵守）。

## 4. 用户评审 8 差距 → 完成映射

| # | 差距 | 修复 Phase | 关键证据 |
|---|---|---|---|
| 1 | Task DAG 未接入 run | 11 | `execution/runner.py` DagRunner；真实 run 输出 DAG 并行任务 |
| 2 | Docker 未接入命令执行 | 12 | 三态 sandbox 接入 shell/tests/lint/验证；无 docker 环境如实降级+计数 |
| 3 | 仓库理解仅 Python / import 图 | 13 | 多语言扫描（3 语言 tree-sitter + 8 语言回退）；符号级调用/引用图 |
| 4 | 语义检索是 LSA 非神经 | 14 | fastembed bge-small 默认；API 可配；LSA 诚实回退 |
| 5 | 中文检索弱 | 15 | jieba/bigram 全路径接入；纯中文 query 从空结果到正确命中 |
| 6 | 索引持久化未复用 | 16 | load_or_build；worktree 播种；逐文件向量缓存；LSA 缓存 bug 修复 |
| 7 | GitHub 流程未产品化 | 17 | issue 命令；push/PR/merge/cleanup 逐步如实报告 |
| 8 | plan --llm 传参错误 | fix/plan-llm-cli | 已并入 repopilot-dev |

## 5. 需求文档 Phase 14-23 → 实现映射

| 文档 | 内容 | 状态 |
|---|---|---|
| §13 Phase 0 | 理解原始项目 | ✅ DEVELOPMENT_RESULTS Phase 0 |
| §14 Phase 1 | Agent Runtime 重构 | ✅ runtime/ |
| §15 Phase 2 | Repository Intelligence | ✅ repo/（含 Phase 13 扩展） |
| §16 Phase 3 | Hybrid Retrieval + Context | ✅ retrieval/（含 Phase 14/15 扩展） |
| §17 Phase 4 | Requirement + Task DAG | ✅ planning/ |
| §18 Phase 5 | Multi-Agent | ✅ agents/ |
| §19 Phase 6 | Git Worktree Isolation | ✅ worktree/ |
| §20 Phase 7 | Verification + Self-Repair | ✅ verify/ |
| §21 Phase 8 | Docker Sandbox + Security | ✅ sandbox/（含 Phase 12 接入） |
| §22 Phase 9 | Evaluation + Benchmark | ✅ evaluation/ |
| §23 Phase 10 | Productization | ✅ product/（含 Phase 17 完整化） |
| §24 Observability | Run/Task 记录 | ✅ runlog（runs.jsonl，真实 run 落盘） |

## 6. 测试覆盖统计（545 项，pytest --collect-only 实测）

| 区域 | 测试数 | 区域 | 测试数 |
|---|---|---|---|
| agents | 32 | retrieval | 93 |
| evaluation | 14 | runtime | 59 |
| execution | 22 | sandbox | 52 |
| planning | 73 | verify | 27 |
| product | 52 | worktree | 26 |
| repo | 90 | 根级（autonomy×2）+fixtures | 5 |

## 7. 真实验收证据索引（每 Phase 一条，详见 DEVELOPMENT_RESULTS.md）

- **P11**：真实 DeepSeek run 走 TaskDAG（多任务并行 worktree）
- **P12**：三态沙箱真实 run；481 测试；发现并修复 as_completed returncode bug
- **P13**：真实 6 文件 4 语言仓库（py/js/ts/go）解析；跨文件调用解析
- **P14**：真实 fastembed 模型 vs LSA 排序对比；缓存 1.8s→0.00s
- **P15**：纯中文 query（修复前空结果）→ 修复后 pricing.py 第一命中
- **P16**：RepoPilot 本体 216 文件 index 1.1s→0.2s；worktree 播种重定位验证；
  合成 1200 文件 3.1x；LSA 缓存命中空结果 bug 修复（可复现→测试锁定）
- **P17**：真实 run $0.0862 → 真实 push 到达裸远端 → gh 缺失诚实报错 →
  cleanup 移除全部 worktree、主工作区字节不变；E2E 锁定 PR/merge argv

## 8. 如实注记（本环境未验证项与限制）

1. **Docker 实机路径 UNVERIFIED**：本机无 docker；三态沙箱的 docker 分支
   有单测锁定（注入 runner），实机容器行为未验证（Phase 8/12 记录）。
2. **gh 实机路径 UNVERIFIED**：本机未安装 gh；PR 创建/合并的 argv 与
   输出有假 gh 单测 + E2E 锁定，真实 GitHub API 行为未验证（Phase 17）。
3. **DeepSeek 无 embeddings API**：探测过（401/404），如实记录；API
   embedding 后端在本环境无可用提供商（Phase 14）。
4. **README_EN.md 为上游原文**：描述 claude-code-from-scratch（原始项目），
   非 RepoPilot——作为上游 README 保留，未改写。
5. **docs/ 为原始项目文档**：00-15 步骤文档来自 baseline，描述原始
   mini-claude 教学项目，未改写。
6. **用户工作区未提交文件保持原样**：根目录两个被删除的 tracked 文档、
   untracked 的 demo/、.python-version、python/mini_claude/ 下的三份
   文档副本——全程未触碰。
7. **main 分支尚未合并 repopilot-dev**：§10 要求 main 保护；PR 已准备
   （见下），gh 可用后执行。
8. 向量缓存是 JSON 明文格式（大仓库约 6MB/1000 文件），未压缩。

## 9. 收尾行动

- PR 描述（repopilot-dev → main）已生成：`.repopilot/pr-repopilot-dev-main.md`
- gh 可用后执行：`gh pr create --repo <owner/repo> --head repopilot-dev
  --base main --title "RepoPilot: Phase 1-17 完整实现" --body-file
  .repopilot/pr-repopilot-dev-main.md`
- 后续可选（文档已标注 ❌）：FastAPI 服务 + Trace Viewer。
