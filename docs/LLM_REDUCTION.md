# 调研笔记：DeepSeek Harness 与程序缩减论文

本文件记录对 DeepSeek Harness 和相关缩减论文的调研，以及可落到 ReproMin
架构上的方向。其中“可选的 LLM 语义 reducer seam”已经作为 opt-in 的
`--semantic-reducer` 实现，详见下文“实现状态”。

## DeepSeek Harness 可借鉴的部分

DeepSeek Harness（`dsh`）是一个 agent runtime，而不是仓库缩减工具。它最
有价值的设计不是具体算法，而是它的扩展模型：

- “一切皆插件”：模型适配器、工具注册、技能、会话、存储、沙箱、Agent
  Loop、调度和 UI 都走统一的插件接口。
- Cordis 微内核：插件只负责加载、卸载和依赖管理；行为封装进插件，服务
  通过 `ctx` 注册，事件通过事件总线分发。
- 依赖拓扑排序、可逆副作用、waterfall 中间件，保证插件组合仍可预测。
- 观测性驱动的 harness 演进：trace、replay、评分都作为一等公民。

对 ReproMin 的启示不是“接入 dsh”，而是把现有的硬编码组件梳理成同一套
能力 seam：

- reducer（Maven、Gradle、Python、Java、files）已经是隐式插件；
- failure oracle 与 runner（host/Docker）也是可替换的能力；
- 报告、checkpoint 和事件流是天然的 trace。

一个现实的做法是：给“语义 reducer”留出与现有 reducer 同构的接口，而不是
把 LLM 调用散落进 scheduler。这样既能借到 dsh 的插件思路，又不需要引入
运行时依赖。

## 论文与算法可借鉴的部分

### LPR：LLM-Aided Program Reduction（ISSTA 2024）

核心思想是“语法缩减器与语义缩减器交替”：

1. 先让语言无关/语法级 reducer（Perses、Vulcan）收敛；
2. 由 LLM 做语言特定的语义变换（删除死代码、内联间接层、合并分支等）；
3. 再把输出交回语法 reducer 继续收敛。

这与 ReproMin 现有的 hierarchical fixed-point scheduler 天然契合：当前
scheduler 会把一个组件接受变更后重新入队其他组件。新增一个 LLM 语义
组件后，`accept -> requeue all` 的循环会自然形成“LLM 变换 + 确定性子
缩减”的交替过程。

### Vulcan（OOPSLA 2023）与 Perses

Vulcan 是语言无关的概率式缩减器，重点是 1-minimality 的更强保证。ReproMin
已经有确定性、可审计的精确统计 gate，但可以借鉴：

- 把“probabilistic candidate family”作为可选加速策略；
- 在报告中记录策略版本，保持与现有 checkpoint 身份同样的可追溯性。

### Hierarchical Delta Debugging 与 TestPrune/SWT-Bench

- HDD 的思想已经体现在文件/目录分层删除和 Java/Python AST 分层候选上。
- TestPrune、SWT-Bench 更偏“复现测试挑选”，对 ReproMin 的启发是在缩减前
  先做测试选择，减少 oracle 成本；这比直接扩大 reducer 更容易产出稳定
  的真实基准。

## 建议的下一步：可选的 LLM 语义 reducer seam

保持零运行时依赖，采用 provider-agnostic 协议：

- 新增 `SemanticReducer` 接口，输入当前树与失败信息，返回一组候选 mutation；
- 默认不启用，不依赖任何 SDK；用户通过环境变量或 CLI 指向本地模型或自
  建 endpoint；
- 候选照旧经过 oracle 验证，失败即丢弃，不会破坏确定性缩减的不变式；
- 报告记录 `semantic_reducer` 策略版本、调用次数和接受次数。

注意：调用外部 LLM API 可能属于付费服务或需要账号，因此在真正接入
第三方 provider 前，应保持 opt-in，并先确认用户选定的模型与授权边界。
可以先实现本地模型/用户自建 endpoint 的适配层，不绑定任何付费服务。

## 参考

- DeepSeek Harness: https://github.com/deepseek-ai/deepseek-harness
- LPR: https://arxiv.org/abs/2312.13064
- Vulcan (OOPSLA 2023): “Pushing the Limit of 1-Minimality of
  Language-Agnostic Program Reduction”
- Perses: syntax-guided program reduction
- Hierarchical Delta Debugging (HDD)

## 实现状态

调研结论已经落地为一个**默认关闭、零运行时依赖、provider-agnostic** 的
语义 reducer seam：

- `repomin.semantic.SemanticBackend` 定义后端协议：`name` 与
  `propose(session) -> Sequence[MutationCandidate]`。
- `NoopSemanticBackend` 是默认实现，返回空候选，运行行为与不启用完全一致。
- `HttpSemanticBackend` 是 OpenAI-compatible chat-completions 适配器，只使用
  标准库 `urllib.request`，不绑定任何 SDK 或付费 provider。
- `SemanticReducer` 把语义候选送进现有的 oracle 管道；被 oracle 拒绝的候选
  照旧丢弃，不会破坏确定性缩减的不变式。

### 用法

```sh
export REPOMIN_SEMANTIC_REDUCER=http
export REPOMIN_SEMANTIC_ENDPOINT=http://localhost:8000/v1/chat/completions
export REPOMIN_SEMANTIC_MODEL=your-local-model
export REPOMIN_SEMANTIC_TOKEN=optional-bearer-token

repomin . \
  --command "python repro.py" \
  --match "ORIGINAL_FAILURE" \
  --semantic-reducer http
```

也可以只通过命令行传入 endpoint、模型名与 `--semantic-timeout`；token 只从
`REPOMIN_SEMANTIC_TOKEN` 环境变量读取，避免出现在 `argv` 或报告中。后端
返回的候选仍然会被 failure oracle 逐条验证，失败即丢弃。报告会在
`execution` 块中记录 `semantic_reducer`、`semantic_model`、
`semantic_endpoint`、`semantic_calls` 与 `semantic_accepted`。
