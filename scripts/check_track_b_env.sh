#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT="${DATA_ROOT:-$ROOT/data}"

missing=()

need_dir() {
  local label="$1"
  local path="$2"
  if [ ! -d "$path" ]; then
    missing+=("$label: $path")
  fi
}

need_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    missing+=("command: $cmd")
  fi
}

need_dir "WebArena data" "${WEBARENA_DATA_DIR:-$DATA_ROOT/webarena}"
need_dir "OSWorld data" "${OSWORLD_DATA_DIR:-$DATA_ROOT/osworld}"
need_dir "tau2-bench" "${TAUBENCH_DIR:-$DATA_ROOT/tau2-bench}"
need_cmd python

if [ -z "${OPENAI_API_KEY:-}" ]; then
  missing+=("env: OPENAI_API_KEY")
fi
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  missing+=("env: ANTHROPIC_API_KEY")
fi

if [ "${CHECK_DOCKER:-1}" = "1" ]; then
  need_cmd docker
fi

if [ "${#missing[@]}" -gt 0 ]; then
  echo "Track-B environment is not ready:"
  printf '  - %s\n' "${missing[@]}"
  exit 1
fi

echo "Track-B environment checks passed."
