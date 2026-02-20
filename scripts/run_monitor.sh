#!/usr/bin/env bash
# run_monitor.sh - cron/launchd から呼び出すラッパースクリプト

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Python仮想環境を有効化
source "$PROJECT_DIR/.venv/bin/activate"

# モニターを実行
python -m suumo_monitor.monitor
