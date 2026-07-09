# Blender Scene Generator

An AI agent that turns natural language descriptions into 3D scenes in Blender.

Built on [oh-my-harness](https://github.com/oh-my-harness/llm-harness-runtime) — showcasing **AgentHarness** and **WorkflowEngine** orchestration capabilities.

> 面向最终用户的使用说明见 [USAGE.md](USAGE.md)。本文档面向开发者。

## Install

The runtime SDK (`llm-harness-py`) is a closed-source PyO3 extension. Prebuilt
wheels are published to the public [llm-harness-py-wheels](https://github.com/oh-my-harness/llm-harness-py-wheels)
repo, so `pip` resolves them directly — no manual download needed.

```bash
git clone https://github.com/oh-my-harness/blender-scene-generator.git
cd blender-scene-generator

# Create a venv (Python 3.12 required)
python3.12 -m venv .venv
source .venv/bin/activate

# Install all deps (runtime wheel resolved from public --find-links URL)
pip install -r blender_scene/requirements.txt \
  --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0

./run.sh
```

## What it does

User types a description (e.g. "a wooden desk with a glass cup and a book, warm lighting") → a multi-step agent workflow plans the scene, builds it in Blender, asks a human to review, and renders the final image.

## Prerequisites

1. **Blender 4.x** installed and on your `PATH` (or set `BLENDER_PATH` to the executable).
2. **Python 3.12** — the runtime SDK (`llm_harness_py`) ships CPython 3.12 wheels. Other versions won't work.
3. **An LLM API key** — OpenAI-compatible (via `OPENAI_API_KEY`) or Anthropic (via `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`).

```bash
# OpenAI (or any OpenAI-compatible endpoint)
export OPENAI_API_KEY="sk-..."
# export OPENAI_MODEL="gpt-4o"

# — or Anthropic —
# export LLM_PROVIDER="anthropic"
# export ANTHROPIC_API_KEY="sk-ant-..."

# Optional, only if `blender` is not on PATH:
# export BLENDER_PATH="/Applications/Blender.app/Contents/MacOS/Blender"
```

## How to run

Two processes must be running: the Blender addon (TCP server) and the Python web server + workflow engine. `run.sh` starts both:

```bash
./run.sh
```

This launches Blender with the addon, waits for the TCP socket, then starts the FastAPI web server. Open the UI at <http://localhost:3000>.

> Order matters: `run.sh` starts Blender first. The server's startup check connects to the addon socket and will refuse to boot otherwise.

## Architecture

```
┌─ Blender GUI ──────────────────┐    ┌─ Browser ──────────────────┐
│  • addon (TCP server)           │    │  • workflow graph (live)   │
│  • real-time 3D viewport        │    │  • agent thinking / tools  │
│  • human reviews the scene here │    │  • human review controls   │
│  • final EEVEE render          │    │  • final render display    │
└─────────────────────────────────┘    └────────────────────────────┘
            ↑ TCP socket                         ↑ WebSocket
            │                                    │
┌─ Python app ─────────────────────────────────────────────────┐
│  Blender Bridge ←→ WorkflowEngine + AgentHarness + Tools    │
│                    ↓ broadcast                               │
│                   Web Server (FastAPI)  ──────────────────────┘
```

**Two windows, two roles:**
- **Blender GUI** — shows what the AI is building (real-time 3D)
- **Browser** — shows how the AI thinks and where the workflow is (orchestration)

### Subsystems

1. **Blender Bridge** (`blender_scene/bridge.py`, `blender_scene/blender_addon.py`) — Python TCP client plus a Blender addon that listens on `127.0.0.1:9876`, runs `bpy` operations, and returns JSON results. Length-prefixed framing, single persistent connection, Blender stays resident so scene state accumulates across tool calls.

2. **Workflow + AgentHarness** (`blender_scene/workflow.py`, `blender_scene/tools.py`) — a 4-step workflow driven by `WorkflowEngine`: `planner → builder → reviewer → renderer`, with a re-hop edge from `reviewer` back to `builder` when the human review fails (capped at 2 rework rounds). Each LLM step builds an `AgentHarness` injected with Blender tools; the reviewer step pauses on `request_human_review` via `EventStream` until the human submits a decision through the web UI.

3. **Web Server** (`blender_scene/server.py`) — a FastAPI app serving the static single-page frontend, a `POST /api/task` endpoint to submit scene descriptions, a `GET /api/render/{filename}` endpoint for rendered images, and a `/ws` WebSocket that broadcasts `WorkflowEvent`s for live graph updates.

4. **Frontend** (`static/index.html`) — zero-dependency single page: an SVG workflow graph that highlights the active step, a streaming event log, human-review controls (active while paused), and the final render display.

### Runtime capabilities showcased

| Capability | Where |
|------------|-------|
| `WorkflowEngine` graph orchestration | 4-step workflow with a re-hop edge |
| Conditional routing (Judge) | `reviewer.passed` → `renderer` or back to `builder` |
| Rework loop with limit | Judge counts builder runs, fails after 2 rework rounds |
| LLM step + Executor step mix | Planner/Builder/Reviewer (LLM) + Renderer (Executor) |
| `AgentHarness` with custom Tools | Each LLM step builds a harness with Blender tools |
| `Tool` trait extension | `add_object`, `set_material`, `execute_python`, `request_human_review`, … |
| Structured step results | `SubmitStepResultTool` JSON consumed by the judge |
| Event stream visualization | `WorkflowEvent` → WebSocket → live SVG graph |
| `EventStream` pause/resume | Human-in-the-loop review |
| Shared blackboard (`WorkflowContext`) | Scene plan + review issues passed between steps |

## Project layout

```
blender-scene-generator/
├── pyproject.toml
├── run.sh                    # launcher: starts Blender + web server
├── blender_scene/
│   ├── __init__.py
│   ├── main.py               # startup: verify Blender, connect bridge, serve web
│   ├── server.py             # FastAPI routes (POST task, GET render, /ws)
│   ├── workflow.py           # 4-step workflow + judge + executors
│   ├── tools.py              # Blender Tool trait impls
│   ├── hooks.py              # NonEmptyResponseHook, SteerOnEmptyHook
│   ├── plugin.py             # SystemPromptPlugin
│   ├── bridge.py             # TCP client to the Blender addon
│   ├── blender_addon.py      # Blender addon: TCP server + bpy operations
│   ├── state.py              # shared AppState
│   └── requirements.txt
├── static/
│   └── index.html            # single-page UI (SVG graph + review controls)
├── blender_scene/tests/      # unit tests
└── examples/                 # scene description examples
```

## Tests

```bash
pip install -e '.[dev]' \
  --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0
pytest
```

The suite includes unit tests for the bridge, tools, workflow, hooks, and server routes.
