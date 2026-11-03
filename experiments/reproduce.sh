#!/usr/bin/env bash
# Run from any directory. Installs the locked environment and regenerates evidence.
set -euo pipefail
cd "$(dirname "$0")"
export UV_CACHE_DIR="${UV_CACHE_DIR:-.cache/uv}"
uv sync --locked
.venv/bin/python -m sim.generate --seed 0
.venv/bin/python -m pytest tests -q --method-results=results/method-results.json
.venv/bin/python -m analysis.report --results results/method-results.json
.venv/bin/python -m analysis.validate_drawdown
