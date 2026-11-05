#!/usr/bin/env bash
# 旧精度 shell 入口；只转交显式请求，不下载、安装或启动模型服务。
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
args=()
[[ -z "${MODEL_PATH:-}" ]] || args+=(--model-path "$MODEL_PATH")
[[ -z "${MODEL_NAME:-}" ]] || args+=(--model-name "$MODEL_NAME")
[[ -z "${HOST:-}" ]] || args+=(--host "$HOST")
[[ -z "${PORT:-}" ]] || args+=(--port "$PORT")
[[ -z "${DATASET:-}" ]] || args+=(--dataset "$DATASET")
if ((${#args[@]})); then
  exec "${PYTHON:-python3}" -B "$script_dir/validator.py" "${args[@]}" "$@"
fi
exec "${PYTHON:-python3}" -B "$script_dir/validator.py" "$@"
