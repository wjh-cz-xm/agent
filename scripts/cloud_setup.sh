#!/usr/bin/env bash
# Run from the repository root. Setup is offline with respect to TronClass.
set -euo pipefail
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/xmu-monitor --help
