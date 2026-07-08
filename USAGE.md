# Blender Scene Generator 使用说明

把一句自然语言描述变成 Blender 里的 3D 场景，并在浏览器里完成审阅与渲染。

> 本文面向**使用者**（只想跑起来生成场景的人）。开发与架构细节请看 [README](README.md)。

---

## 1. 它能做什么

- 输入一句场景描述（例如「一张木质书桌，桌上有一只玻璃杯和一本书，暖色灯光」）。
- AI 自动完成 **规划 → 建模 → 审阅 → 渲染** 四步工作流。
- 全程在 Blender 视口里实时看到模型被搭出来。
- 渲染完成后在浏览器里查看最终图片，并可继续提出修改意见微调场景。

## 2. 运行前需要准备

| 依赖 | 说明 |
|------|------|
| **Blender 4.x** | 从 [blender.org](https://www.blender.org/download/) 安装。默认查找 PATH 和 macOS 默认位置；若不在，设置环境变量 `BLENDER_PATH` 指向可执行文件。 |
| **Python 3.12** | 运行时 SDK 仅发布 CPython 3.12 wheel，其他版本不可用。从 [python.org](https://www.python.org/downloads/) 安装。 |
| **OpenAI 兼容的 LLM API** | 需要 `OPENAI_API_KEY`。可直连 OpenAI，也可指向任何 OpenAI 兼容网关（如 DeepSeek、本地推理服务）。可选设置 `OPENAI_API_BASE`、`OPENAI_MODEL`。 |

## 3. 安装

闭源 runtime SDK 的 wheel 发布在公开的 [llm-harness-py-wheels](https://github.com/oh-my-harness/llm-harness-py-wheels) 仓库，`pip` 通过 `--find-links` 直接解析，无需手动下载：

```bash
git clone https://github.com/oh-my-harness/blender-scene-generator.git
cd blender-scene-generator

python3.12 -m venv .venv
source .venv/bin/activate

# 安装全部依赖（runtime wheel 从公开 --find-links URL 解析）
pip install -r blender_scene/requirements.txt \
  --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0

./run.sh
```

## 4. 配置 LLM

在终端 export，或写入 `.env` 文件（启动时会自动加载同目录的 `.env`）：

```bash
# 直连 OpenAI
export OPENAI_API_KEY="sk-..."
export OPENAI_API_BASE="https://api.openai.com"
export OPENAI_MODEL="gpt-4o"

# 或指向任意 OpenAI 兼容服务
# export OPENAI_API_BASE="https://api.deepseek.com"
# export OPENAI_MODEL="deepseek-chat"
```

> 如果 Blender 不在 PATH 也不在 macOS 默认位置，加上：
> ```bash
> export BLENDER_PATH="/path/to/blender"
> ```

## 5. 启动

一个命令搞定——`run.sh` 会自动启动 Blender、加载插件、等待就绪、启动 Web 服务：

```bash
./run.sh
```

你会看到 Blender GUI 弹出，终端显示：

```
▶ Starting Blender with addon...
✓ Addon ready
▶ Starting web server on http://localhost:3000 ...
```

浏览器打开 <http://localhost:3000>，开始用。

停止：按 `Ctrl+C`，Blender 与 Web 服务一起退出。

## 6. 使用 Web 界面

打开 <http://localhost:3000>，界面分左右两栏。

### 左栏 — 操作区

1. **Scene Description**：在文本框里输入场景描述，点 **Generate Scene** 提交。
2. **Workflow**：一个 SVG 流程图，实时高亮当前执行到哪一步：
   - `Planner` → `Builder` → `Reviewer` → `Render` → `Wait`
   - 审阅不通过会从 `Reviewer` 回到 `Builder`（最多 2 轮返工）。
   - 渲染完成后停在 `Wait`，等待你的修改意见，可循环回到 `Builder` 继续微调。
3. **Step Details**：每个步骤的详细卡片，展示 LLM 思考输出与工具调用进度。
4. **Review**：当流程暂停在审阅环节时显示。可查看审阅摘要，点 **Approve** 通过、**Reject** 打回（可在下方填写反馈）。
5. **Render Result**：渲染完成后显示最终图片。
6. **Adjust Scene**：渲染后出现。输入修改指令（如「把沙发改成红色，桌上加一盏台灯」），点 **Apply Adjustment**，流程会回到 `Builder` 重新建模 → 渲染。

### 右栏 — 事件流

按时间顺序实时打印工作流事件（步骤开始/结束、暂停、恢复、失败等），方便观察 AI 当前在做什么。

## 7. 同时观察 Blender 视口

Blender GUI 窗口要保持打开。建模阶段你会看到物体被逐个添加到场景里——这是 AI 正在调用 `add_object`、`set_material`、`add_light` 等工具。**不要手动操作 Blender**，避免与 AI 的工具调用冲突。

## 8. 场景描述怎么写更好

写得越具体，结果越可控。建议包含：

- **物体清单**：明确列出要出现的物体及其材质（「一张橡木书桌，一只透明玻璃杯，一本红色封面精装书」）。
- **空间关系**：说明摆放位置（「杯子放在书桌右上角，书本平放在桌面中央」）。
- **光照**：暖光/冷光、方向、强度（「从左上方射入的暖色阳光」）。
- **镜头**：视角与构图（「从斜上方 45° 俯视桌面」）。

反例（太模糊）：「一个房间」。
正例：「一间北欧风格的小客厅，浅灰墙，一张白色布艺沙发靠左墙，前方可可色圆形茶几，沙发旁一盏黑色落地灯，自然光从右侧窗户照入」。

## 9. 示例：雨夜霓虹小巷

下面是一个完整示例，演示如何用结构化描述生成复杂场景。

### 渲染结果

![雨夜霓虹小巷渲染结果](docs/examples/rainy_neon_alley.png)

### 提示词

> 完整文本见 [examples/rainy_neon_alley.md](examples/rainy_neon_alley.md)，对应工程文件 [docs/examples/rainy_neon_alley.blend](docs/examples/rainy_neon_alley.blend)。

```text
雨夜霓虹小巷：

一条狭窄的城市后巷,刚下过暴雨。地面是湿漉漉的深灰色水泥,积水的洼地倒映着头顶错落的霓虹招牌。巷子两侧是老旧的混凝土建筑,墙面贴满层层叠叠、边角翘起的海报,管道和空调外机裸露在外。

建筑
- 左侧墙:高约 8 米的混凝土墙面,一条竖向裂缝从顶部延伸到中部。墙根有一扇锈蚀的金属卷帘门,半开着,透出暖橙色灯光。墙上挂着一个空调外机,下方滴水。
- 右侧墙:比左侧矮,约 6 米。嵌着一扇发光的霓虹招牌——日式拉面店的"暖簾"样式,粉红色霓虹管弯成弧形文字。招牌下方是一扇玻璃门,门内是亮着灯的店铺,暖黄色光从门缝透出。
- 巷子尽头:一堵实心砖墙封死,墙面爬满枯藤。砖墙前停着一辆旧自行车,车身上有锈迹。

地面与积水
- 水泥地面有网状裂纹,裂缝里嵌着青苔
- 巷子中央有一片约 2 平方米的积水洼,水面微微起伏,倒映霓虹招牌的粉红色和招牌上方一盏路灯的冷白色
- 卷帘门前有一小摊水迹,水滴从门上方的外机持续滴落,在地面激起细小涟漪

霓虹招牌
- 粉红色霓虹管,弯成弧形,安装在右侧墙面中段
- 其中一段霓虹管接触不良,以约 2 秒为间隔明灭闪烁
- 招牌下方的玻璃门门框上有一条细长的 LED 灯带,稳定亮着暖黄色

光照
- 主光:巷子上方一盏路灯,冷白色,从高处斜照下来,在地面拉出长条状的高光
- 霓虹招牌:粉红色点光源,照亮招牌周围 2 米半径的墙面
- 卷帘门内:暖橙色光,从半开的门缝溢出,照亮门前的水迹
- 店铺内:暖黄色光,从玻璃门透出
- 无环境光——小巷两侧建筑高耸,几乎无天光进入,所有照明来自人造光源

大气
- 雨刚停,空气中有薄雾,路灯和霓虹光在雾中形成光晕
- 远处巷口隐约可见街道车灯的流动光斑

相机
低角度,位于巷子入口处,相机高度约 1.2 米,略低于人眼视角。朝向巷子深处,构图引导线由两侧墙面汇聚向巷尾砖墙。前景是地面积水的倒影,中景是两侧的霓虹招牌与卷帘门,远景是被雾模糊的砖墙与自行车。
```

### 为什么这个提示词效果好

这份描述在极有限的空间里压缩了密集的视觉信息，每一个几何决策都有重量：

- **结构化分块**：建筑 / 地面 / 招牌 / 光照 / 大气 / 相机，逐项展开，Planner 能直接映射到场景对象。
- **具体尺寸与位置**：墙高 8 米、积水 2 平方米、相机 1.2 米——给 Builder 明确的几何约束，而非含糊的「一堵墙」。
- **材质与光源明确**：湿漉漉的水泥（高金属度、低粗糙度）、粉红霓虹管（emission 着色器）、暖橙/暖黄/冷白多色温光源交织。
- **动态细节**：明灭闪烁的霓虹、滴水涟漪、薄雾光晕——考验 Builder 对工具的组合能力（`add_curve` 做弧形霓虹管、`boolean_modify` 做墙面裂缝、材质参数做镜面反射积水）。
- **构图意图**：低角度、引导线汇聚、前/中/远景层次，给相机设置明确方向。

对照你的描述，若缺少其中某类信息，AI 会自行补全——结果可能偏离预期。补全越具体，可控性越高。


## 10. 输出文件

- `renders/render_<时间戳>.png` —— 每次渲染的最终图片。
- `sessions/` —— 工作流会话记录（用于调试与回放）。

每次启动会清空这两个目录，请提前保存需要的产物。

## 11. 常见问题

**启动报「cannot connect to Blender addon」**
Blender 没启动或插件没监听 `9876`。确认 Blender 窗口已弹出且未卡住，重新运行 `./run.sh`。

**`blender executable not found`**
`BLENDER_PATH` 没指对。确认路径指向 Blender 的可执行文件本体（macOS 上是 `.../Contents/MacOS/Blender`，不是 `.app` 目录）。

**`llm_harness_py not installed`**
依赖没装。运行 `pip install -r blender_scene/requirements.txt --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0`。确认 Python 版本是 3.12。

**LLM 调用失败 / 超时**
检查 `OPENAI_API_KEY` 与 `OPENAI_API_BASE` 是否正确、网络是否能访问该端点。`.env` 里可切换不同 provider 对比测试。

**渲染出来是黑屏**
多半是灯光或相机没设置好。可以在「Adjust Scene」里输入「增加一盏面光源照亮场景，相机对准桌面」让 AI 修正。

**审阅一直不通过、反复返工**
工作流限制最多 2 轮返工，超过会失败。可在描述里把需求写得更明确，或主动用「Adjust Scene」给出具体修改指令。

## 12. 停止

按 `Ctrl+C`，Blender 与 Web 服务一起退出。

---

如需了解内部架构、工作流拓扑、工具清单，请参阅 [README](README.md)。
