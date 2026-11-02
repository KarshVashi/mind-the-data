# ADR-001: Stack

Date: 2026-11-02
Status: accepted

## Context

The Tube League Table needs TfL line status, disruptions and arrival predictions captured every minute from the first week, because none of it can be backfilled. Then it turns that into a weekly ranking. Constraints:

- **Cost:** under £5 a month in total.
- **Unattended:** the system must run without a person on hand, and alert when collection breaks.
- **Reproducible:** every published number must be reproducible from raw data.
- **One engineer:** one person builds and runs everything.

The workload is small and bursty: about 50 HTTP calls a minute taking a few seconds, tens of GB of raw JSON a year, and one daily batch transform.

## Decision

| Area | Choice |
|---|---|
| Language | Python 3.12, `uv` for environment and lockfile |
| HTTP and contracts | `httpx` async with bounded concurrency and retries; Pydantic v2 models per TfL response |
| Ingestion | AWS Lambda (arm64) triggered every minute by EventBridge Scheduler, in `eu-west-2`, not in a VPC |
| Raw storage | S3, gzipped NDJSON, Hive-style `dt`/`hour` partitions, kept indefinitely (ADR-003) |
| Catalog | Glue Data Catalog with partition projection, no crawlers |
| Query engine | Athena engine v3 |
| Table format | Apache Iceberg for every curated table |
| Transformation | dbt Core with `dbt-athena-community`; `dev` and `prod` targets in separate Glue databases |
| Orchestration | GitHub Actions cron (daily transform, weekly publish), OIDC role into AWS |
| Infrastructure | Terraform, S3 backend with native lockfile, one module per concern |
| CI/CD | Lint, types, tests, `dbt parse`, `terraform validate` on every PR; `terraform plan` on PRs and `apply` on `main` behind a protected `production` environment |
| Secrets | TfL app key in SSM Parameter Store (SecureString) |
| Monitoring | CloudWatch alarms to SNS email |
| Publishing | Static JSON and PNG to Cloudflare R2; website rebuilt by a Cloudflare Pages deploy hook |

## Alternatives considered

- **ECS Fargate always-on poller.** This is only needed if arrivals must be polled more often than once a minute. ADR-002 keeps it as the fallback.
- **Firehose straight to Iceberg.** It adds cost, and it hides the raw payload, which is the one irreplaceable asset.
- **Snowflake.** Credit minimums are out of proportion to a workload of tens of GB.
- **DuckDB or MotherDuck.** Technically just as viable. Athena keeps compute next to the data with IAM-only access, and needs no second account.
- **Hive Parquet tables with insert-overwrite.** Iceberg MERGE gives idempotent incrementals, schema evolution, time travel for debugging, and compaction.
- **MWAA, Dagster+ or Step Functions for orchestration.** MWAA costs hundreds of pounds a month, and Dagster+ is paid hosting. Step Functions works but adds a second deployment surface. GitHub Actions is free for this repo and its runs are visible and re-runnable.
- **CDK.** Terraform was preferred for portability and for its readable plan output, which is reviewed on each PR.
- **Secrets Manager.** $0.40 a month per secret, with nothing gained over SSM for one static key.
- **A NAT gateway or VPC-attached Lambdas.** A NAT gateway alone would cost more than everything else combined.

## Consequences

- **Cost:** the always-free Lambda allowance covers the collector, and the estimated total is £1.50 to £3.50 a month in year one.
- **Small files:** writing an object per feed per minute creates many small files. Staging reads one day at a time with partition projection, and curated tables are compacted weekly (`OPTIMIZE` and `VACUUM`).
- **No VPC:** Lambdas run outside a VPC, so access control rests entirely on IAM.
- **Scheduling:** GitHub Actions cron can run late. The weekly publish is scheduled early enough to absorb that, and can be triggered by hand.
- **Revisit:** if Athena scan cost or latency grows past expectations, or if ADR-002 moves arrivals to 30-second polling.
