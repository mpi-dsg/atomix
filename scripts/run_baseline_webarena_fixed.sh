#!/bin/bash
# WebArena improved baseline: pinned gpt-4o snapshot, temp=0
set -e

ATOMIX_ROOT="${ATOMIX_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
set -a; source "$ATOMIX_ROOT/.env"; set +a

RESULT_DIR="$ATOMIX_ROOT/results/webarena_baseline_fixed"
mkdir -p "$RESULT_DIR"

cd "$ATOMIX_ROOT/workloads/webarena"

# Test gpt-4o snapshots - starting with 2024-05-13
# Other snapshots to try: 2024-08-06, 2024-11-20
MODEL="gpt-4o-2024-05-13"
TEMP=0.0
MAX_STEPS=30
TASK_IDS=(0 1 2 3 5 11 15 25 30 45)

echo "=== WebArena Improved Baseline ==="
echo "Model: $MODEL (pinned snapshot)"
echo "Temperature: $TEMP (deterministic)"
echo "Max Steps: $MAX_STEPS"
echo "Tasks: ${TASK_IDS[@]}"
echo ""

# Track results
TOTAL_SUCCESS=0
TOTAL_TASKS=${#TASK_IDS[@]}

for i in "${!TASK_IDS[@]}"; do
    TASK_ID="${TASK_IDS[$i]}"
    echo "[$((i+1))/$TOTAL_TASKS] Running task $TASK_ID..."

    mkdir -p "$RESULT_DIR"
    python3 "$ATOMIX_ROOT/scripts/run_webarena_atomix.py" \
        --mode No-Tx \
        --model "$MODEL" \
        --temperature "$TEMP" \
        --max-steps "$MAX_STEPS" \
        --fault-probability 0.0 \
        --task-ids "$TASK_ID" \
        --result-dir "$RESULT_DIR" \
        --output "$RESULT_DIR/task_${TASK_ID}.json" \
        > "$RESULT_DIR/task_${TASK_ID}.log" 2>&1 &
    PID=$!
    echo "    Task $TASK_ID started (PID: $PID), waiting..."
    wait $PID 2>/dev/null || true
    sleep 1

    if [ -f "$RESULT_DIR/task_${TASK_ID}.json" ]; then
        SUCCESS=$(python3 -c "import json; d=json.load(open('$RESULT_DIR/task_${TASK_ID}.json')); print(d.get('successes', 0))")
        echo "    Result: success=$SUCCESS"
        TOTAL_SUCCESS=$((TOTAL_SUCCESS + SUCCESS))
    else
        echo "    Result: ERROR (no output file)"
    fi
done

echo ""
echo "=== Final Results ==="
echo "Successes: $TOTAL_SUCCESS/$TOTAL_TASKS"
python3 -c "print(f'Success Rate: {$TOTAL_SUCCESS/$TOTAL_TASKS:.1%}')"

# Write summary
cat > "$RESULT_DIR/summary.json" <<EOF
{
  "model": "$MODEL",
  "temperature": $TEMP,
  "max_steps": $MAX_STEPS,
  "total_tasks": $TOTAL_TASKS,
  "successes": $TOTAL_SUCCESS,
  "success_rate": $(python3 -c "import sys; print(f'{$TOTAL_SUCCESS/$TOTAL_TASKS}')"),
  "snapshots_tested": ["$MODEL"],
  "timestamp": "$(date -Iseconds)"
}
EOF

echo "Summary written to $RESULT_DIR/summary.json"
echo "=== WebArena Baseline done ==="
