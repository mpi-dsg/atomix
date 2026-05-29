#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
cd "$ROOT"

MODE=${1:-full}
RUN_SETUP=${RUN_SETUP:-0}
RESULTS_DIR=${RESULTS_DIR:-results/experiments}

# Load environment
if [ -f ".env" ]; then
  set -a
  . ./.env
  set +a
fi

# Set defaults
DATA_ROOT=${DATA_ROOT:-$ROOT/data}
export DATA_ROOT

# Run setup if requested
if [ "$RUN_SETUP" = "1" ]; then
  echo "Running setup (downloading data and installing dependencies)..."
  export PIP_BREAK_SYSTEM_PACKAGES=1
  SKIP_SWEBENCH=1 SETUP_TAUBENCH=1 SETUP_OSWORLD=1 bash scripts/download_data.sh

  echo "Starting services (Docker containers for WebArena)..."
  bash scripts/start_services.sh
fi

if [ "$MODE" = "v3-local" ]; then
  echo "Skipping Track-B data validation for v3-local synthetic/control bundle."
else
  # Validate required data directories exist BEFORE running any experiments
  echo "Validating data directories..."
  MISSING=()

  WEBARENA_DIR="${WEBARENA_DATA_DIR:-$DATA_ROOT/webarena}"
  if [ ! -d "$WEBARENA_DIR" ]; then
    MISSING+=("webarena: $WEBARENA_DIR")
  fi

  TAUBENCH_DIR="${TAUBENCH_DIR:-$DATA_ROOT/tau2-bench}"
  if [ ! -d "$TAUBENCH_DIR" ]; then
    MISSING+=("tau2-bench: $TAUBENCH_DIR")
  fi

  OSWORLD_DIR="${OSWORLD_DATA_DIR:-$DATA_ROOT/osworld}"
  if [ ! -d "$OSWORLD_DIR" ]; then
    MISSING+=("osworld: $OSWORLD_DIR")
  fi

  SETUP_VENV="${DATA_ROOT}/.atomix-setup-venv"
  if [ ! -d "$SETUP_VENV" ]; then
    MISSING+=("setup-venv: $SETUP_VENV")
  fi

  if [ ${#MISSING[@]} -gt 0 ]; then
    echo "ERROR: Missing required data directories:"
    for dir in "${MISSING[@]}"; do
      echo "  - $dir"
    done
    echo ""
    echo "Run with RUN_SETUP=1 to download and install dependencies:"
    echo "  RUN_SETUP=1 bash scripts/run_experiments_bundle.sh $MODE"
    exit 1
  fi
  echo "All data directories validated."
fi

# Create results directories
mkdir -p "$RESULTS_DIR" "results/plots"

EXIT_CODE_FILE="$RESULTS_DIR/exit_code.txt"
trap 'echo $? > "$EXIT_CODE_FILE"' EXIT

# Select configs based on mode
if [ "$MODE" = "v3-local" ]; then
  CONFIGS=(
    configs/experiments/b1_clean_success.json
    configs/experiments/b2_multiagent.json
    configs/experiments/b2_composition_ablations.json
    configs/experiments/b3_irreversible.json
    configs/experiments/b3_fp_sweep_real_sink.json
    configs/experiments/b5_ports.json
    configs/experiments/b6_spec_webarena.json
    configs/experiments/e5_b1_semantic.json
    configs/experiments/e5_b2_frontier_contract.json
    configs/experiments/e5_b3_crash_window.json
    configs/experiments/e6_aliasing.json
    configs/experiments/e7_compfail.json
    configs/experiments/e8_granularity.json
    configs/experiments/e9_correlated.json
    configs/experiments/e10_annotation_errors.json
    configs/experiments/b9_agent_faults.json
  )
  echo "Running v3 local bundle"
elif [ "$MODE" = "smoke" ]; then
  CONFIGS=(
    configs/experiments/smoke_e3_microbench.json
    configs/experiments/smoke_e3_microbench_sequential.json
    configs/experiments/smoke_e3_microbench_conflict.json
    configs/experiments/smoke_e3_out_of_order.json
    configs/experiments/smoke_e3_speculative_discard.json
    configs/experiments/smoke_e3_contention_sweep.json
    configs/experiments/smoke_e2_speculation.json
    configs/experiments/smoke_e2_webarena.json
    configs/experiments/smoke_e1_taubench.json
    configs/experiments/smoke_e2_osworld.json
  )
  echo "Running smoke bundle"
else
  CONFIGS=(
    configs/experiments/e1_parallel.json
    configs/experiments/e1_taubench.json
    configs/experiments/e2_speculation.json
    configs/experiments/e2_webarena.json
    configs/experiments/e2_webarena_faults_low.json
    configs/experiments/e2_webarena_faults_high.json
    configs/experiments/e2_osworld.json
    configs/experiments/e2_osworld_faults_low.json
    configs/experiments/e2_osworld_faults_high.json
    configs/experiments/e3_microbench.json
    configs/experiments/e3_microbench_correctness_cliff.json
    configs/experiments/e3_microbench_sequential.json
    configs/experiments/e3_microbench_conflict.json
    configs/experiments/e3_out_of_order.json
    configs/experiments/e3_speculative_discard.json
    configs/experiments/e3_contention_sweep.json
    configs/experiments/e4_frontier_ablation.json
  )
  echo "Running full bundle"
fi

# Run experiments
FAILURES=()
MODES=("Tx-Full" "No-Frontier" "No-Tx")

for config in "${CONFIGS[@]}"; do
  name=$(basename "$config" .json)
  if [ "$MODE" != "v3-local" ] && [[ "$name" == *"webarena"* || "$name" == *"taubench"* || "$name" == *"osworld"* || "$name" == *"speculation"* || "$name" == *"speculative"* ]]; then
    for mode in "${MODES[@]}"; do
      output_name="${name}_${mode}"
      if ! python scripts/run_experiment_dispatch.py --config "$config" --output "$RESULTS_DIR/${output_name}.json" --mode "$mode"; then
        FAILURES+=("$output_name")
      fi
      echo "Finished ${output_name}"
    done
  else
    if ! python scripts/run_experiment_dispatch.py --config "$config" --output "$RESULTS_DIR/${name}.json"; then
      FAILURES+=("$name")
    fi
    echo "Finished $name"
  fi
done

# Aggregate results
python scripts/aggregate_experiment_results.py --input-dir "$RESULTS_DIR" --output results/summary.json --csv results/summary.csv
python scripts/plot_experiment_results.py --input results/summary.json --output-dir results/plots

# Report results
if [ ${#FAILURES[@]} -gt 0 ]; then
  printf "%s\n" "${FAILURES[@]}" > "$RESULTS_DIR/failures.txt"
  echo "Completed with failures: ${FAILURES[*]}"
  exit 1
else
  echo "Completed successfully"
fi

echo "Done. Results: $RESULTS_DIR, summary: results/summary.json, plots: results/plots"
exit 0
