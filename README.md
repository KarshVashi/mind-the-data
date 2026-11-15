# mind-the-data

London rail reliability data products. The first is the **Tube League Table**: a weekly ranking of London's Tube, Elizabeth line, DLR, Overground and tram lines, built from TfL's live API.

Each line is ranked two ways:

- **Declared view, the Disruption Index:** what TfL's status feed said, minute by minute, weighted by severity and time of day.
- **Measured view, Experienced Reliability:** how long passengers waited beyond the timetable, inferred from arrival boards at about 50 stations.

The headline is the **Honesty Gap**: hours in which a line showed Good Service while the boards recorded a long gap between trains.

**Live table:** `TODO: karshvashi.com/tube/ once the weekly publish is switched on`

## How it works

```text
TfL API ──every minute──► Lambda poller ──► S3 raw (gzipped NDJSON, kept forever)
                                                │  Glue catalog, partition projection
                                                ▼
                       dbt on Athena (daily) ──► Iceberg tables: line-minutes, incidents,
                                                │  inferred arrivals, headways, weekly scores
                                                ▼
                 weekly export (Mondays) ──► JSON + share cards ──► Cloudflare R2 ──► website
```

1. A Lambda polls TfL every minute for line status, disruptions and the arrival boards at about 50 measurement stations, and stores every response unchanged, failures included, so missing data is never mistaken for good service.
2. A daily GitHub Actions job runs dbt on Athena, turning raw snapshots into Iceberg tables: the status of every line in every minute, incidents, inferred train arrivals, headways against the timetable, and daily and weekly scores per line.
3. Every Monday a second job exports the latest complete week as schema-validated JSON, renders share cards in headless Chromium, uploads both to Cloudflare R2 and triggers a rebuild of the website.
4. On the 2nd of each month the same job publishes the month as an open dataset: minute-level declared status and the daily and weekly facts, as Parquet, with a README and a checksummed manifest.

It is designed to run for under £5 a month, unattended, with CloudWatch alarms and daily health checks. The first two weeks project to about 1.80 USD a month before VAT ([docs/cost.md](docs/cost.md)). Every Sunday the staging tables are compacted and vacuumed.

- [docs/architecture.md](docs/architecture.md): the components, data flow and security model.
- [docs/methodology.md](docs/methodology.md): how every number is computed, and its known limits.
- [docs/site-integration.md](docs/site-integration.md): the published files and how the website reads them.
- [docs/runbook.md](docs/runbook.md): operating it (alarms, re-runs, backfills, key rotation), and the steps only the owner can do.
- [docs/cost.md](docs/cost.md): what it costs, from Cost Explorer.

## Running it

Requirements: [uv](https://docs.astral.sh/uv/), Terraform 1.10 or later, [tflint](https://github.com/terraform-linters/tflint). `make check` needs network access: it builds the Lambda package and initialises the Terraform and tflint plugins.

```sh
git clone https://github.com/KarshVashi/mind-the-data.git && cd mind-the-data
make install      # Python 3.12 environment from uv.lock, and the pre-commit hook
make check-fast   # ruff, mypy, unit tests
make check        # the full gate CI runs: also sqlfluff, dbt parse, terraform validate, tflint
make help         # every target
```

Tests never call TfL or AWS: they run on recorded fixtures in `tests/fixtures/`.

**Try the publish step offline,** from recorded mart results:

```sh
uv run playwright install chromium   # once, for the share cards
uv run python -m mind_the_data.publish.export \
  --recorded tests/fixtures/marts --week 2026-W45 --allow-incomplete --out-dir build/publish
```

That writes the league, line, methodology and index JSON files, validated against `src/mind_the_data/publish/contract/`, and the two PNG cards under `build/publish/`.

**Against AWS.** Reading real data needs the dev identity and the settings in `env.example` (runbook, H11 and "dbt connection settings"); then `make dbt-build-dev` builds the models into the dev database. Deploying is done only by CI: merging to `main` applies the reviewed Terraform plan.

## Layout

| Path | Contents |
| --- | --- |
| `src/mind_the_data/` | ingest Lambdas, arrival-inference reference, publish code, ops checks |
| `transform/` | dbt project (Athena, Iceberg): seeds, staging, intermediate, marts, tests |
| `infra/` | Terraform: `bootstrap/` (human-applied CI roles), `modules/`, `envs/prod/` |
| `config/` | lines and measurement stations |
| `tests/` | unit and integration tests, recorded fixtures |
| `docs/` | architecture, methodology, runbook, ADRs, decisions, data-quality reviews |

## Design decisions

Each has a short record in [docs/adr/](docs/adr/). The main ones:

| Decision | Record |
| --- | --- |
| Lambda + S3 + Athena/Iceberg + dbt + GitHub Actions, for under £5 a month | [ADR-001](docs/adr/001-stack.md) |
| Poll every 60 s; revisit after the week-one review | [ADR-002](docs/adr/002-polling-interval.md) |
| Raw is gzipped NDJSON envelopes with failures recorded | [ADR-003](docs/adr/003-raw-format.md) |
| Overlapping statuses: the highest weight wins | [ADR-004](docs/adr/004-multiple-statuses.md) |
| CI roles owned by a human-applied bootstrap; merging to `main` is the deploy approval | [ADR-005](docs/adr/005-ci-role-ownership.md) |
| OIDC trust on the repository's immutable id, bound to each workflow file | [ADR-009](docs/adr/009-oidc-immutable-subjects.md), [ADR-010](docs/adr/010-workflow-bound-ci-roles.md) |
| Low-risk pull requests merge through a gate that always runs `main`'s rules | [ADR-011](docs/adr/011-merge-gate.md) (proposed) |
| Metric models rebuild in full from the seeds, so a definition change applies to all history | [ADR-012](docs/adr/012-run-models-rebuild-in-full.md) (proposed) |

Metric definitions that the plan left open are listed in [docs/DECISIONS.md](docs/DECISIONS.md) with what was implemented and the alternatives. The methodology marks which ones are still provisional.

## Status

See [docs/STATUS.md](docs/STATUS.md) for detail.

- **Collecting** every minute since 4 November 2026.
- **Declared view** (Disruption Index): built and running daily in prod.
- **Measured view** (Experienced Reliability, Honesty Gap): built, but its quality gate is at 65.6% of station-hours in band against a target of 80%, so it is not published yet.
- **Publishing:** export, share cards, R2 upload and the weekly workflow are built, and the owner has agreed to publish with provisional labels (D36). Going live waits for the Cloudflare setup (runbook, H12).
- **Operations:** weekly Iceberg maintenance, a full refresh through CI, and the monthly open dataset are built. The open data waits for its licence decision (D63) and H12.

## What's next

- Pass the measured view's quality gate: vehicle-id linking on more Tube lines, then re-run the review.
- League mechanics: monthly divisions with promotion and relegation, a season table from April, a strikes-excluded variant, and a sensitivity analysis of the weights.
- The cost report for the first full month (December), replacing the partial-month projection.
- Later: passenger weighting from station entry and exit counts, scaling part suspensions by the affected section, a cause breakdown of disruption reasons, and validation against TfL's own operated-trips figures.

Data: Powered by TfL Open Data.
