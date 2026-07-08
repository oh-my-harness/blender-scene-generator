# CLAUDE.md

## 项目概述

Blender Scene Generator：基于 oh-my-harness runtime 的 3D 场景生成工具。
通过自然语言描述生成 Blender 场景，支持交互式调整。

## 依赖规则

### Runtime 不可修改

`llm-harness-runtime` 和 `llm-api-adapter` 是外部闭源依赖。本仓库通过预编译 PyO3 wheel 消费 runtime，不接触其 Rust 源码。

- **可以**：阅读 runtime 源码理解行为、定位 bug、提出 bug 报告
- **不能**：修改 runtime 的任何源码文件
- **不能**：将 path 依赖（本地 `../llm-harness-runtime` venv）作为长期方案，仅用于本地调试

如果发现 runtime 有 bug，在项目侧用 hook、plugin、wrapper 等方式绕过，
不要直接改 runtime 源码。需要修改 runtime 时，先向用户说明问题并征求同意。
如果觉得 runtime 不好用（API 设计不合理、缺少功能、文档不清晰等），
同样将问题记录在 `docs/runtime-feedback.md`，便于后续统一反馈给 runtime 维护者。

### 依赖配置

- runtime SDK (`llm_harness_py`) 是闭源 PyO3 扩展（CPython 3.12）。源码在私有仓库 `llm-harness-runtime`，预编译 wheel 发布在公开仓库 `llm-harness-py-wheels`
- 安装：`pip install -r blender_scene/requirements.txt --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0`
- 本地调试：在 `../REDACTED/llm-harness-py` 下 `maturin develop --release`，然后用 `./run-dev.sh` 启动（自动用该 venv）

## 构建

本仓库是纯 Python，无需编译。安装依赖即可：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r blender_scene/requirements.txt \
  --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0
```

测试：

```bash
pytest
```

## 运行

```bash
./run.sh        # pip 安装的依赖（用户/生产）
./run-dev.sh    # 本地 maturin develop 的 runtime（开发，改 Rust 源码后实时生效）
```

- Blender 路径：`/Applications/Blender.app/Contents/MacOS/Blender`
- Web 服务器：`http://localhost:3000`
- Blender addon TCP：`127.0.0.1:9876`
- Session 日志：`sessions/sub-agents/`
- 渲染输出：`renders/`

## LLM 配置

`.env` 文件配置：`OPENAI_API_KEY`、`OPENAI_API_BASE`、`OPENAI_MODEL`。
当前使用 `glm-5.2`，地址 `http://api.REDACTED.com/`。

glm-5.2 已知行为：
- 返回 `reasoning` 字段（非标准 `reasoning_content`），需 `parse_reasoning_content(true)`
- 第一个 streaming chunk 的 `content` 为空字符串 `""`（非 null）
- 偶尔只输出 reasoning 不输出 content text，需 `NonEmptyResponseHook` + `SteerOnEmptyHook` 兜底
- 不一定主动调用 `submit_step_result`，judge 需容忍 `structured` 为空

## 架构

### 工作流

统一 workflow，adjust 复用同一 engine 实例（step_history + context 跨轮累积）：

```
planner → builder → reviewer → renderer → wait_for_adjust → builder → renderer → wait_for_adjust → ...
                      ↑________________________|  (review failed, rework)
```

- **生成**：Planner → Builder → Reviewer → Renderer → Wait（阻塞等用户）
- **调整**：Wait → Builder(增量修改, 读 `adjustment_instruction`) → Renderer → Wait（阻塞等用户）
- Judge 在 builder 第 2+ 次执行时跳过 Reviewer 直接到 Renderer
- `wait_for_adjust` 是 Executor step（`WaitForAdjustExecutor`），用 `EventStream` 阻塞等 `/api/adjust` 推入指令

### 关键设计

- Blender addon 所有 bpy 操作通过 `bpy.app.timers` 调度到主线程执行
- `NonEmptyResponseHook`：LLM 返回空 content 时重试（最多 3 次）
- `SteerOnEmptyHook`：空响应重试时注入 user 消息督促 LLM 调用工具，打破"相同 prompt → 相同空响应"循环
- Judge 在 `structured` 为空时默认 `passed=true`（容忍 glm-5.2 不调 submit_step_result）
- `AppState` 持有 `WorkflowEngine` + `AdjustHandle`，engine 跨 adjust 轮次常驻
