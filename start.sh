#!/usr/bin/env bash
# One command to run PartnerFlow Lab:   ./start.sh
# Creates the Python environment the first time, then starts the API and dashboard.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        echo "PartnerFlow Lab needs Python 3.10 or newer. Found: $(python3 --version 2>&1)"
        exit 1
    fi
    echo "First run: creating the Python environment (needs internet once) ..."
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements.txt
fi

exec .venv/bin/python -u run.py "$@"
