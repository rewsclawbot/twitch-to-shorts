#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
[[ -f .env ]] && set -a && source .env && set +a
python scripts/rotate_streamers.py --execute 2>&1
