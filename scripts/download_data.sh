#!/usr/bin/env bash
set -euo pipefail

# Downloads/installs workload datasets into DATA_ROOT.
# Requires: curl, git, python3, pip (host network access).

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
DATA_ROOT=${DATA_ROOT:-"$ROOT/data"}
mkdir -p "$DATA_ROOT"

SETUP_VENV_DIR=${SETUP_VENV_DIR:-$DATA_ROOT/.atomix-setup-venv}
SKIP_WEBARENA=${SKIP_WEBARENA:-0}

log() { echo "[download] $*"; }

if [ ! -d "$SETUP_VENV_DIR" ]; then
  python3 -m venv "$SETUP_VENV_DIR"
fi
SETUP_PY="$SETUP_VENV_DIR/bin/python"

# WebArena
if [ "$SKIP_WEBARENA" != "1" ]; then
  WEBARENA_DIR="${WEBARENA_DATA_DIR:-$DATA_ROOT/webarena}"
  if [ ! -d "$WEBARENA_DIR" ]; then
    log "Cloning WebArena (full dataset) -> $WEBARENA_DIR"
    git clone --depth=1 https://github.com/web-arena-x/webarena.git "$WEBARENA_DIR"
    (cd "$WEBARENA_DIR" && bash prepare.sh || true)
  else
    log "WebArena already present at $WEBARENA_DIR"
  fi

  # WebArena dependencies
  log "Installing WebArena deps"
  "$SETUP_PY" -m pip install --upgrade pip
  # Install our required deps first
  "$SETUP_PY" -m pip install playwright openai anthropic beautifulsoup4 rapidfuzz requests_toolbelt pydrive pydrive2 beartype
  # Then install WebArena requirements (may downgrade some deps)
  if [ -f "${WEBARENA_DIR}/requirements.txt" ]; then
    "$SETUP_PY" -m pip install -r "${WEBARENA_DIR}/requirements.txt" || true
  fi
  # Re-ensure critical deps are at working versions
  "$SETUP_PY" -m pip install beartype openai anthropic
  "$SETUP_PY" -m playwright install chromium || true

  # Generate WebArena config files (requires URL env vars set)
  if [ -n "${REDDIT:-}" ] && [ -n "${SHOPPING:-}" ]; then
    log "Generating WebArena config files"
    (cd "$WEBARENA_DIR" && PYTHONPATH="$WEBARENA_DIR" "$SETUP_PY" scripts/generate_test_data.py || true)
  else
    log "Skipping WebArena config generation (set REDDIT/SHOPPING/etc env vars)"
  fi
else
  log "Skipping WebArena setup (SKIP_WEBARENA=1)"
fi

# OSWorld (real environment)
if [ "${SETUP_OSWORLD:-0}" = "1" ]; then
  OSWORLD_DIR="${OSWORLD_DATA_DIR:-$DATA_ROOT/osworld}"
  if [ ! -d "$OSWORLD_DIR/.git" ]; then
    log "Cloning OSWorld -> $OSWORLD_DIR"
    git clone --depth=1 https://github.com/xlang-ai/OSWorld.git "$OSWORLD_DIR"
  fi
  log "Installing OSWorld deps"
  if [ -f "${OSWORLD_DIR}/requirements.txt" ]; then
    "$SETUP_PY" -m pip install -r "${OSWORLD_DIR}/requirements.txt" || true
  fi
  "$SETUP_PY" -m pip install anthropic beautifulsoup4 rapidfuzz requests_toolbelt pydrive pydrive2 formulas
else
  log "Skipping OSWorld setup (SETUP_OSWORLD=1 to enable)"
fi


# TheAgentCompany environment setup
if [ "${SETUP_THEAGENTCOMPANY:-0}" = "1" ]; then
  log "Setting up TheAgentCompany (Docker services + data)"
  THEAGENTCOMPANY_DIR="${THEAGENTCOMPANY_DIR:-$DATA_ROOT/theagentcompany}"
  THEAGENTCOMPANY_SETUP_URL=${THEAGENTCOMPANY_SETUP_URL:-https://github.com/TheAgentCompany/the-agent-company-backup-data/releases/download/setup-script-20241208/setup.sh}
  mkdir -p "$THEAGENTCOMPANY_DIR"
  curl -fsSL "$THEAGENTCOMPANY_SETUP_URL" -o "$THEAGENTCOMPANY_DIR/setup.sh"
  (cd "$THEAGENTCOMPANY_DIR" && bash setup.sh)
  touch "$THEAGENTCOMPANY_DIR/.tac_setup_done"
else
  log "Skipping TheAgentCompany setup (SETUP_THEAGENTCOMPANY=1 to enable)"
fi

# tau2-bench setup (optional)
if [ "${SETUP_TAUBENCH:-0}" = "1" ]; then
  log "Setting up tau2-bench"
  TAUBENCH_DIR="${TAUBENCH_DIR:-$DATA_ROOT/tau2-bench}"
  TAUBENCH_REPO_URL=${TAUBENCH_REPO_URL:-https://github.com/sierra-research/tau2-bench}
  if [ ! -d "$TAUBENCH_DIR/.git" ]; then
    git clone "$TAUBENCH_REPO_URL" "$TAUBENCH_DIR"
  fi
  (cd "$TAUBENCH_DIR" && python3 -m venv .venv && source .venv/bin/activate && python -m pip install --upgrade pip && python -m pip install -e . && .venv/bin/tau2 check-data || true)
  touch "$TAUBENCH_DIR/.tau2_setup_done"
else
  log "Skipping tau2-bench setup (SETUP_TAUBENCH=1 to enable)"
fi

log "Download script complete"
