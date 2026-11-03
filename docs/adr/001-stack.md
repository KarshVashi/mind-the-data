# ADR-001: Stack

Date: 2026-11-02
Status: accepted

## Context

The Tube League Table needs TfL line status, disruptions and arrival predictions captured every minute from the first week, because none of it can be backfilled. Then it turns that into a weekly ranking. Constraints:

- **Cost:** under £5 a month in total.
- **Unattended:** the system must run without a person on hand, and alert when collection breaks.
- **Reproducible:** every published number must be reproducible from raw data.
- **One engineer:** one person builds and runs everything.

The workload is about 5 seconds of I/O per minute, tens of GB of raw JSON, and one daily batch transform. Region is `eu-west-2` (London), in one AWS account with one infrastructure environment (`prod`).

## Decision

| Area | Choice | Why | Alternatives considered |
| --- | --- | --- | --- |
| Language | Python 3.12, `uv` for environment and lockfile | One language for ingest, tests and publishing; fast reproducible installs | Poetry (slower), pip-tools |
| Ingestion compute | AWS Lambda (arm64) on EventBridge Scheduler, 1-minute rate, not in a VPC | Fits Lambda's always-free allowance; no servers to patch | ECS Fargate always-on task (needed only for 30 s polling, see ADR-002), Glue Python shell (no benefit) |
| HTTP | `httpx` async with bounded concurrency; retries with backoff and jitter | About 50 calls a minute must finish in seconds | `requests` with threads |
| Payload contracts | Pydantic v2 models for each TfL response | Fails loudly if TfL changes shape; models double as documentation | Unvalidated dicts |
| Raw storage | S3, gzipped NDJSON, one object per feed per poll, Hive-style partitions (ADR-003) | Lossless, replayable, cheap; raw is irreplaceable, so it is kept indefinitely | Firehose to Iceberg (adds cost and hides the raw payload), DynamoDB |
| Catalog | Glue Data Catalog with partition projection on raw | No crawler cost or lag; partitions resolve from the path | Glue crawlers |
| Query engine | Athena engine v3 | Serverless, pay per scan, native Iceberg MERGE; data volume is tens of GB | Snowflake (credit minimums for a tiny workload), DuckDB or MotherDuck (equally viable technically; Athena keeps compute next to the data with IAM-only access) |
| Table format | Apache Iceberg for all curated tables | MERGE for idempotent incrementals, schema evolution, time travel for debugging, OPTIMIZE and VACUUM for small files | Hive Parquet tables with insert-overwrite |
| Transformation | dbt Core with `dbt-athena-community`; `dev` and `prod` targets in separate Glue databases | Tested, documented, lineage-tracked SQL; unit tests for metric logic | Hand-written Athena SQL in Step Functions |
| Data quality | dbt generic and singular tests, dbt unit tests, source freshness, a weekly completeness model | Quality checks live with the models they guard | Great Expectations (heavier for this size) |
| Orchestration | GitHub Actions cron (daily `dbt build`, weekly publish); OIDC role into AWS | No orchestrator to host; runs are visible and re-runnable | MWAA (hundreds of pounds a month), Step Functions (fine, but a second deployment surface), Dagster+ (paid hosting) |
| Infrastructure | Terraform, S3 backend with native lockfile, one module per concern | Everything reproducible from the repo | CDK |
| CI/CD | GitHub Actions: lint, types, tests, `dbt parse`, `terraform validate` and `plan` on every PR; `terraform apply` when a PR is merged into `main`; the merge is the approval (ADR-005) | Infra changes are reviewed as plans before they apply | Applying from a laptop; a `production` environment with a required reviewer (unavailable on a free private repository) |
| Secrets | TfL app key in SSM Parameter Store (SecureString, standard tier) | Free; Lambda reads it at cold start | Secrets Manager ($0.40 a month per secret, no extra value here) |
| Monitoring | CloudWatch alarms (poller errors, poller not writing for 10 minutes, Lambda throttles) to SNS email | Silent data loss is the main risk | Third-party monitoring |
| Publishing | Static JSON and PNG to Cloudflare R2; website rebuilt by a Cloudflare Pages deploy hook | The site is already on Cloudflare Pages; R2 egress is free | S3 with CloudFront, committing data into the website repo |
| Share card | HTML template rendered to PNG with Playwright (Chromium) | Tube colours and typography are easier in HTML and CSS | matplotlib, Pillow |
| Tooling | `ruff` (lint and format), `mypy --strict` on `src/`, `pytest`, `sqlfluff` (Athena dialect, dbt templater), `pre-commit`, `tflint` | Fast, standard, enforced in CI | |

No NAT gateway, ever. It would cost more than everything else combined.

## Consequences

- **Cost:** the estimate is about £1.50 to £3.50 a month in year one, with the always-free Lambda allowance covering the collector.
- **Cost guards:**
  - an AWS Budget of £5 with alerts at 50%, 80% and 100%
  - an Athena workgroup with a 10 GB per-query scan limit
  - a 7-day lifecycle on Athena query results
  - no VPC-attached Lambdas
- **GitHub Actions minutes:** they cost nothing while the repository is public, or while a private repository stays inside its monthly free minutes.
- **Small files:** writing one object per feed per minute creates many small files. Staging reads one day at a time with partition projection, and curated tables are compacted weekly.
- **Scheduling:** GitHub Actions cron can run late. The weekly publish is scheduled with slack before posting, and can also be triggered by hand.
