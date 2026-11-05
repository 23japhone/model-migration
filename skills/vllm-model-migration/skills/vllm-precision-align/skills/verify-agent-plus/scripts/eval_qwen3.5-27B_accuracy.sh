#!/usr/bin/env bash
# 历史文件名兼容入口，不隐式指定模型、数据集或服务。
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$script_dir/run_accuracy.sh" "$@"
