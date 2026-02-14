#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -x ".venv/bin/python" ]; then
    echo "FATAL: Missing virtualenv Python at .venv/bin/python" >&2
    exit 1
fi

source .venv/bin/activate

EXPECTED_PYTHON="3.12"
PYTHON_VERSION="$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$PYTHON_VERSION" != "$EXPECTED_PYTHON" ]; then
    echo "FATAL: Expected Python $EXPECTED_PYTHON, got $PYTHON_VERSION" >&2
    exit 1
fi

if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

mkdir -p data
PIPELINE_TRIGGER=local python main.py 2>&1 | tee -a data/pipeline.log
