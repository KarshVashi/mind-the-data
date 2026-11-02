# mind-the-data

London rail reliability data products. The first is the **Tube League Table**: a weekly reliability ranking of London rail lines built from TfL's live API, ranking each line by what TfL declares (Disruption Index) and by what passengers experience (Experienced Reliability).

The build plan, architecture, metric definitions and milestones are in [docs/PLAN.md](docs/PLAN.md). Decisions are recorded in [docs/adr/](docs/adr/).

## Getting started

Requirements: [uv](https://docs.astral.sh/uv/), Terraform 1.10 or later, tflint.

```sh
make install      # create the Python 3.12 environment from uv.lock
make check-fast   # ruff, mypy, unit tests
make check        # the full gate CI runs
make help         # every target
```

## Layout

| Path | Contents |
| --- | --- |
| `src/mind_the_data/` | ingest Lambdas, shared helpers, publish code |
| `transform/` | dbt project (Athena, Iceberg) |
| `infra/` | Terraform: `bootstrap/` (one-off), `modules/`, `envs/prod/` |
| `config/` | lines and measurement stations |
| `tests/` | unit and integration tests, recorded TfL fixtures |
| `docs/` | plan, ADRs, runbook, methodology |
