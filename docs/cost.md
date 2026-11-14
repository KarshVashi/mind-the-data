# Cost

**Partial month, projected run-rate.** The plan asks for the first full month (M7). The owner asked for the figure sooner, from the partial month, so this page covers 1 to 13 November 2026 and projects a month from the days since the pipeline settled. Re-run it after 1 December for the full month (commands at the end).

All figures are AWS Cost Explorer, unblended, in USD, read on 2026-11-14. Day 14 is left out because Cost Explorer has not finished it.

## Summary

| | USD |
| --- | --- |
| Account, 1 to 13 November | 1.30 |
| of which tagged `project = mind-the-data` (from 8 November) | 0.81 |
| Steady-state day, 10 to 12 November (tagged) | 0.057 |
| **Projected 30-day month, before VAT** | **about 1.80** |
| Projected 30-day month, with 20% VAT | about 2.20 |
| AWS Budget (H5): £5, set as | 6.50 |

The projection is the steady-state day x 30, plus storage growing through the month. Dev work makes it higher: 13 November, a day of dev queries, cost 0.12 USD, and a month of days like it would be about 3.60 USD before VAT. Either way it is inside the plan's estimate of £1.50 to £3.50 and the budget.

## By service, 1 to 13 November

| Service | USD | Note |
| --- | --- | --- |
| Amazon S3 | 0.907 | requests, almost all of it (below); 0.661 tagged |
| Tax | 0.210 | VAT, posted on 1 November |
| Amazon Athena | 0.163 | 32.6 GB scanned; 0.152 tagged |
| AWS Cost Explorer | 0.010 | the API calls behind this page |
| Amazon ECR | 0.007 | not this project (untagged, no ECR in `infra/`) |
| AWS Lambda, Glue, KMS, SNS, CloudWatch, EventBridge | 0.000 | inside the free tier |
| **Total** | **1.296** | |

S3 by usage type:

| Usage type | Quantity | USD |
| --- | --- | --- |
| PUT, COPY, POST, LIST (Tier 1) | 108,565 requests | 0.575 |
| GET and other reads (Tier 2) | 703,852 requests | 0.296 |
| Storage | 1.16 GB-month | 0.033 |
| Data transfer out | 1.3 GB | 0.000 (free allowance) |

## Steady state

From 10 November the collector, the daily transform and the ops checks ran with little dev work. A tagged day then costs:

| Item | Per day | USD per day | Per 30 days |
| --- | --- | --- | --- |
| S3 PUT (Tier 1) | 7,189 requests | 0.038 | 1.15 |
| S3 GET (Tier 2) | ~31,000 requests | 0.013 | 0.39 |
| Athena | 1.1 GB scanned | 0.0056 | 0.17 |
| S3 storage | ~4 GB stored, growing | 0.003 | ~0.10 |
| **Total** | | **0.057** | **~1.80** |

**What drives it.** S3 PUT requests are two thirds of the bill. The poller writes about five objects a minute (one per feed per poll, ADR-003), about 216,000 a month, against the plan's estimate of 140,000 (PLAN 11). They cost about 1.15 USD a month and do not grow. Storage grows by about 0.2 GB a day and is the only line that rises month on month; it reaches the plan's figure of about £1.50 a month after a year. Athena in prod is pennies: staging reads two days of raw a run.

## Dev Athena scans inflate the partial month

The first two weeks included the week-one review, the M4 gate rechecks and dbt runs against the dev database, all through the dev workgroup. Athena's per-workgroup metrics for 1 to 13 November (CloudWatch `AWS/Athena ProcessedBytes`, successful queries):

| Workgroup | GB processed |
| --- | --- |
| `mind-the-data-dev` | 16.9 |
| `mind-the-data-prod` | 6.7 |

So about 72% of the scans the workgroups recorded came from dev work. The dev queries also read raw objects directly, which shows up as S3 GET requests: 241,858 on 8 November and 190,384 on 9 November, against about 31,000 on a quiet day. 8 and 9 November together cost 0.53 USD, 40% of the period. The steady-state rows above leave those days out; a month of heavy analysis would cost more, not the pipeline.

## Caveats

- The `project` cost allocation tag shows from 8 November only. From 4 to 7 November the collector's S3 costs are in the untagged figure.
- The account holds another project's resources (ECR, the `wiki-lake-dev` workgroup). They are small and untagged, and are left out of the tagged figures.
- GitHub Actions and Cloudflare are free at this size and not in Cost Explorer.

## Re-running

Read-only, with the dev profile. Cost Explorer is a global service and answers in `us-east-1`. The end date is exclusive.

```sh
export AWS_PROFILE=mtd-dev
# By service:
aws ce get-cost-and-usage --region us-east-1 \
  --time-period Start=2026-11-01,End=2026-12-01 --granularity MONTHLY \
  --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE
# The project's tagged spend by usage type, per day:
aws ce get-cost-and-usage --region us-east-1 \
  --time-period Start=2026-11-01,End=2026-12-01 --granularity DAILY \
  --metrics UnblendedCost UsageQuantity --group-by Type=DIMENSION,Key=USAGE_TYPE \
  --filter '{"Tags":{"Key":"project","Values":["mind-the-data"]}}'
```

The workgroup split comes from CloudWatch (`aws cloudwatch get-metric-data` with the search expression `SEARCH('{AWS/Athena,QueryState,QueryType,WorkGroup} MetricName="ProcessedBytes"', 'Sum', 86400)` in `eu-west-2`).
