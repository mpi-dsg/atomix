# Workloads

Atomix includes adapters and experiment runners for external benchmark
workloads. The core package does not require these dependencies unless you run
the corresponding experiments.

## Supported Workloads

- WebArena: browser task suite backed by Playwright and Docker services.
- OSWorld: desktop task suite with local and VM-backed harnesses.
- SWE-bench: software engineering task harness.
- tau2-bench: conversational tool-use benchmark.
- TheAgentCompany: optional Dockerized software-company environment.

## Data And Dependencies

Copy `.env.example` to `.env` and set the paths or API keys required by the
workloads you intend to run.

```bash
cp .env.example .env
bash scripts/download_data.sh
```

`scripts/download_data.sh` prepares local workload directories under
`DATA_ROOT` unless a workload-specific environment variable is set:

- `WEBARENA_DATA_DIR`
- `OSWORLD_DATA_DIR`
- `SWE_BENCH_DATA_DIR`
- `TAUBENCH_DIR`
- `THEAGENTCOMPANY_DIR`

The `workloads/osworld` and `workloads/webarena` directories are tracked as git
submodules for upstream reference. Runtime scripts primarily resolve workload
data from the environment variables above.

## WebArena Services

Run:

```bash
bash scripts/start_services.sh
```

The standard local ports are 7770, 7780, 9999, 8023, and 8888. The corresponding
service URLs are documented in `.env.example`.

## Runners

- `scripts/run_experiments_bundle.sh` runs grouped smoke, full, or local bundles.
- `scripts/run_experiment_dispatch.py` routes JSON configs to the correct runner.
- `scripts/run_workload_experiment.py` runs WebArena, OSWorld, TheAgentCompany,
  and tau2-bench configs.
- `scripts/run_microbenchmarks.py` runs deterministic contention benchmarks.
- `scripts/run_speculation_sim.py` runs deterministic speculation simulations.

## Configs

- Workload task configs live in `configs/workloads/`.
- Experiment configs live in `configs/experiments/`.
- Generated outputs should be written under `results/` or `runs/`; both are
  ignored by git.
