#!/usr/bin/env bash
# Run with pip-installed dependencies (end user / production).
#
# Prerequisites:
#   python3.12 -m venv .venv
#   source .venv/bin/activate
#   pip install -r blender_scene/requirements.txt \
#     --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0
set -euo pipefail

PYTHON="${PYTHON:-python3}"

INSTALL_HELP() {
    cat <<'EOF'

  Install dependencies first:

    python3.12 -m venv .venv
    source .venv/bin/activate
    pip install -r blender_scene/requirements.txt \
      --find-links https://github.com/oh-my-harness/llm-harness-py-wheels/releases/expanded_assets/v0.2.0

  Then run: ./run.sh
EOF
}

if ! command -v "$PYTHON" &>/dev/null; then
    echo "✗ Python not found: $PYTHON"
    echo "  Requires Python 3.12."
    INSTALL_HELP
    exit 1
fi

if ! "$PYTHON" -c "import llm_harness_py" 2>/dev/null; then
    echo "✗ llm_harness_py not installed for $PYTHON"
    INSTALL_HELP
    exit 1
fi

source "$(dirname "$0")/scripts/_run_common.sh"
