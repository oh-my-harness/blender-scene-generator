#!/usr/bin/env bash
# Common startup logic shared by run.sh and run-dev.sh.
# The caller must set $PYTHON before sourcing this script.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# ── Config ────────────────────────────────────────────────────
BLENDER_PATH="${BLENDER_PATH:-/Applications/Blender.app/Contents/MacOS/Blender}"
ADDON="$(pwd)/blender_scene/blender_addon.py"
ADDR="127.0.0.1:9876"
PORT="3000"
ENV_FILE="${ENV_FILE:-.env}"

# ── Load .env ─────────────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
fi

# ── Check Blender executable ──────────────────────────────────
if ! command -v "$BLENDER_PATH" &>/dev/null && [ ! -x "$BLENDER_PATH" ]; then
    echo "✗ Blender not found at: $BLENDER_PATH"
    echo "  Set BLENDER_PATH or install Blender."
    exit 1
fi

# ── Check API key ─────────────────────────────────────────────
case "${LLM_PROVIDER:-openai}" in
    anthropic)
        if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
            echo "✗ LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY not set. Configure it in $ENV_FILE."
            exit 1
        fi
        echo "✓ Blender:   $BLENDER_PATH"
        echo "✓ Python:    $PYTHON"
        echo "✓ Provider:  anthropic"
        echo "✓ Model:     ${ANTHROPIC_MODEL:-claude-sonnet-4-20250514}"
        echo "✓ API base:  ${ANTHROPIC_API_BASE:-https://api.anthropic.com}"
        ;;
    openai|*)
        if [ -z "${OPENAI_API_KEY:-}" ]; then
            echo "✗ OPENAI_API_KEY not set. Configure it in $ENV_FILE."
            echo "  (or set LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY to use Anthropic)"
            exit 1
        fi
        echo "✓ Blender:   $BLENDER_PATH"
        echo "✓ Python:    $PYTHON"
        echo "✓ Provider:  openai"
        echo "✓ Model:     ${OPENAI_MODEL:-gpt-4o}"
        echo "✓ API base:  ${OPENAI_API_BASE:-https://api.openai.com}"
        ;;
esac
echo ""

# ── Clean previous output ─────────────────────────────────────
rm -rf sessions/ renders/

# ── Start Blender + addon ─────────────────────────────────────
pkill -f "blender.*addon.py" 2>/dev/null || true
sleep 0.5

echo "▶ Starting Blender with addon..."
"$BLENDER_PATH" --python "$ADDON" &
BLENDER_PID=$!

# ── Wait for addon socket ─────────────────────────────────────
echo "  Waiting for addon to listen on $ADDR..."
for i in $(seq 1 30); do
    if nc -z 127.0.0.1 9876 2>/dev/null; then
        echo "✓ Addon ready"
        break
    fi
    if ! kill -0 "$BLENDER_PID" 2>/dev/null; then
        echo "✗ Blender process exited unexpectedly"
        exit 1
    fi
    sleep 0.5
done

if ! nc -z 127.0.0.1 9876 2>/dev/null; then
    echo "✗ Addon did not start within 15s"
    kill "$BLENDER_PID" 2>/dev/null || true
    exit 1
fi

echo ""
echo "▶ Starting web server on http://localhost:$PORT ..."
echo "  Open browser → http://localhost:$PORT"
echo "  Press Ctrl+C to stop both."
echo ""

# ── Start web server (foreground) ─────────────────────────────
export BLENDER_PATH
export PYTHONPATH="${PYTHONPATH:-}:$(pwd)"
"$PYTHON" -m blender_scene.main &
SERVER_PID=$!

# ── Cleanup on exit ───────────────────────────────────────────
cleanup() {
    echo ""
    echo "■ Shutting down..."
    kill "$SERVER_PID" 2>/dev/null || true
    kill "$BLENDER_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    echo "✓ Done"
}
trap cleanup EXIT INT TERM

# ── Wait for either process to exit ───────────────────────────
while kill -0 "$SERVER_PID" 2>/dev/null && kill -0 "$BLENDER_PID" 2>/dev/null; do
    sleep 1
done
