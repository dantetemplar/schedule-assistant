# Schedule assistant

CP-SAT (OR-Tools) timetabling: reads a **YAML schedule config**, writes a solved schedule and optional solver logs under `results/`.

## Setup

Requires **Python ≥ 3.12**. Dependencies are declared in `pyproject.toml`; install with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

Run commands with `uv run …` so they use the project environment (examples below).


## Generate configs 

```bash
uv run instructors_roster.py
uv run convert_json_to_config_candidate.py
```

`instructors_roster.py` reads People/exportUsers CSV rosters and writes `instructors.yaml` (same schema as schedule-builder-backend instructors config).

`convert_json_to_config_candidate.py` reads `core-courses-lessons-sum-2026.yaml` and merges `electives-lessons-sum-2026.yaml` from the project directory (or cwd). Override paths with positional `core_courses_yaml` and `electives_yaml`.

```bash
uv run tests/cases/generate_cases.py
```

## Run the solver

Solver and metrics were moved to **schedule-builder-backend** (`scripts/solve.py`, `scripts/metrics.py`) and use the same `ScheduleConfig` schemas as the API.

From `schedule-builder-backend`:

```bash
uv sync --group solver
uv run --group solver python scripts/solve.py path/to/config.yaml --no-progress
```

**Output:** prints `status` and `stats` to stdout. Writes the full result to:

`results/<YYYY-mm-dd_HH-MM-SS>_<term-slug>/output.yaml` or `--artifacts-dir <path>`

Solver phase logs may appear as `solver_log_phase_*.txt` in that same folder.

For experiments better to pass `--artifacts-dir <path>` to avoid clobbering the default `results/` directory and to be able to run several experiments in parallel.

## Check metrics

```bash
cd ../schedule-builder-backend
uv run --group solver python scripts/metrics.py \
  --config ../schedule-assistant/tests/cases/feasible_by_program_year_block1/core_year_1.yaml \
  --solution "$(ls -1t results/*/output.yaml | head -n1)"
```

For machine-readable output:

```bash
uv run --group solver python scripts/metrics.py \
  --config path/to/config.yaml \
  --solution results/<timestamp>_<term-slug>/output.yaml \
  --json
```

Use the `output.yaml` from the same `scripts/solve.py` run (under `results/...`).

## Get cpsat-primer examples and README.md

```bash
git clone https://github.com/d-krupke/cpsat-primer --depth 1
cd cpsat-primer
mv README.md ../cpsat-primer-README.md
mv examples ../cpsat-primer-examples
cd ..
rm -rf cpsat-primer
```

## Tests

```bash
uv run pytest # NOTE THEY VERY SLOW
```
