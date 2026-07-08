#!/usr/bin/env bash
# Run with maturin-developed runtime (local dev).
#
# Uses the llm-harness-runtime venv where `maturin develop --release` was run.
# This lets you iterate on Rust runtime source and test changes immediately.
#
# If the venv or llm_harness_py is missing, this script bootstraps them automatically.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_CRATE="$SCRIPT_DIR/../llm-harness-runtime/crates/llm-harness-py"
VENV_DIR="$RUNTIME_CRATE/.venv"
PYTHON="$VENV_DIR/bin/python3"
MATURIN="$VENV_DIR/bin/maturin"

# ── Bootstrap venv + maturin if missing ───────────────────────
if [ ! -x "$PYTHON" ]; then
    echo "▶ Creating venv at $VENV_DIR ..."
    python3.12 -m venv "$VENV_DIR"
    echo "▶ Installing maturin ..."
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install maturin
fi
# ── Build llm_harness_py if not importable ────────────────────
if ! "$PYTHON" -c "import llm_harness_py" 2>/dev/null; then
    echo "▶ Building llm_harness_py via maturin develop --release ..."
    (cd "$RUNTIME_CRATE" && "$MATURIN" develop --release)
fi

# ── Install app-level deps (fastapi, uvicorn, ...) ────────────
if ! "$PYTHON" -c "import fastapi" 2>/dev/null; then
    echo "▶ Installing app dependencies ..."
    "$PYTHON" -m pip install fastapi uvicorn[standard] websockets httpx
fi

echo "✓ Runtime ready: $PYTHON"
echo ""

source "$(dirname "$0")/scripts/_run_common.sh"
