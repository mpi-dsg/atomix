Atomix Project Brief
====================

Purpose
-------
Build a transactional runtime for LLM tool use with frontier-aligned commits. The goal is reproducible, fault-tolerant experiments across WebArena, OSWorld, and tau2-bench plus deterministic microbench/speculation suites.

Core concepts
-------------
- Transactions: tools record effects and commit/abort as a unit.
- Frontiers: per-resource progress gates commit order.
- Replay: deterministic effect logs enable replays and comparisons.

Repo layout
-----------
- `src/atomix/`: runtime, transaction manager, frontier tracking, adapters.
- `src/atomix/integrations/`: workload harnesses.
- `scripts/`: experiment runners and setup utilities.
- `configs/experiments/`: smoke/full configs and microbench/speculation configs.
- `configs/workloads/`: workload task configs.

Experiments
-----------
- Smoke/full bundles via `scripts/run_experiments_bundle.sh`.
- Microbenchmarks and speculation suites via `scripts/run_experiment_dispatch.py`.
- Aggregation and plots via `scripts/aggregate_experiment_results.py` and `scripts/plot_experiment_results.py`.
