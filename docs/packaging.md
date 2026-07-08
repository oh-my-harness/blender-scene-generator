# 打包与分发设计

## 目标

- **本仓库** (`blender-scene-generator`) 开源：Python 应用源码、前端、Blender addon、启动脚本全部公开。
- **Runtime** (`llm-harness-runtime`) 闭源：Rust 实现的 WorkflowEngine / AgentHarness / 工具链，源码不公开。
- **Wheels** (`llm-harness-py-wheels`) 开源仓库：仅存放编译后的 PyO3 wheel 二进制，无源码。
- 最终用户无需 Rust 工具链，无需接触 runtime 源码，`pip install` 直接拉到 wheel。

## 三仓库关系

```
┌─ llm-harness-runtime (private) ──────────────────────────┐
│  Rust workspace (源码)                                   │
│  └─ crates/llm-harness-py/  →  maturin build  →  .whl   │
│                                                          │
│  ./scripts/build-wheels.sh <ver> → build + upload       │
└──────────────────────────┬───────────────────────────────┘
                           │ prebuilt wheel (closed-source binary)
                           ▼
┌─ llm-harness-py-wheels (public) ─────────────────────────┐
│  编译后的 wheel 二进制，无源码                            │
│  GitHub Release 资产作为 pip --find-links 源             │
└──────────────────────────┬───────────────────────────────┘
                           │ pip install --find-links <url>
                           ▼
┌─ blender-scene-generator (public, open source) ──────────┐
│  Python app (blender_scene/) + static/ + addon + run.sh  │
│  用户: git clone → pip install → ./run.sh                │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
                     end user
```

## 安装路径

本仓库开源，用户直接 clone。Runtime wheel 在公开的 `llm-harness-py-wheels` 仓库 release 里，`pip` 通过 `--find-links` 直接解析，无需手动下载：

```bash
git clone https://github.com/oh-my-harness/blender-scene-generator.git
cd blender-scene-generator
python3.12 -m venv .venv && source .venv/bin/activate

pip install -r blender_scene/requirements.txt \
  --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0

./run.sh
```

## 平台矩阵

Runtime wheel（CPython 3.12）：

| 平台 | wheel 标签 | 状态 |
|------|-----------|------|
| macOS Apple Silicon | `cp312-cp312-macosx_11_0_arm64` | ✅ 已构建 |
| macOS Intel | `cp312-cp312-macosx_10_12_x86_64` | ✅ 已构建 |
| Linux x64 | `cp312-cp312-manylinux_2_28_x86_64` | ⏳ 待构建（需 Linux 机器） |
| Windows x64 | `cp312-cp312-win_amd64` | ⏳ 待构建（需 Windows 机器） |

> macOS wheel 在 Apple Silicon 开发机上用 `maturin build --release` 交叉编译产出。Linux/Windows 需各自平台的原生工具链（Linux musl 静态链接需 `cargo-zigbuild` 或 Linux 容器；Windows 需 MSVC）。

## 版本约定

- 三个仓库**共享版本号**：`v0.2.0` 的 runtime 源码 → `v0.2.0` 的 wheel → 本仓库依赖 `v0.2.0`。
- Runtime 发新版 → `./scripts/build-wheels.sh <version>` 构建 wheel 并上传到 `llm-harness-py-wheels` 仓库 release → 本仓库更新 `requirements.txt` 和 `pyproject.toml` 中的版本号。
- `pyproject.toml` 和 `requirements.txt` 中的 `--find-links` URL 硬编码版本号，升级时同步修改。

## 构建流程（本地脚本，无 CI）

### Runtime 仓库：构建 wheel

```bash
cd ~/Documents/projs/oh-my-harness/llm-harness-runtime
./scripts/build-wheels.sh 0.2.0               # 构建 macOS wheel + 上传到 wheels 仓库 release
./scripts/build-wheels.sh 0.2.0 --build-only  # 仅构建，不上传
```

macOS（Apple Silicon + Intel）在 Apple Silicon 机器上交叉编译。Linux/Windows 需在各自平台上 `maturin build` 后手动 `gh release upload` 到 `llm-harness-py-wheels` 仓库。

## 保密性分析

| 资产 | 含源码？ | 分发渠道 | 风险 |
|------|---------|---------|------|
| 本仓库 git | 是（Python 源码） | public repo | 无——开源 |
| Runtime git | 是（Rust 源码） | private repo | 无——不公开 |
| Wheels 仓库 | 否（仅编译二进制） | public repo | 低——可反编译但无源码，符合"保密"要求 |
| Wheels 仓库 release | 否（编译二进制） | public release | 同上 |

关键点：runtime 的 Rust 源码永远不会出现在任何公开渠道。wheel 是编译产物，虽然理论上可被反编译/逆向，但这符合"runtime 代码不能开源，需要保密"的要求——保密 ≠ 防逆向，而是不主动公开源码。wheels 仓库公开使得 `pip install` 无需认证即可拉取，用户体验与纯开源项目一致。

## 已知限制

- **CPython ABI 锁定**：wheel 绑定 CPython 3.12。若需支持 3.13+，构建脚本需扩展 `--interpreter` 维度。
- **Linux/Windows wheel**：需在各自平台原生构建，macOS 无法交叉编译。
- **Linux sandbox**：runtime 的 `bwrap` sandbox 需要系统 bubblewrap；如需沙箱，用户自行安装。当前本仓库应用未启用 sandbox，无影响。
- **版本同步**：手动同步三个仓库版本号。
