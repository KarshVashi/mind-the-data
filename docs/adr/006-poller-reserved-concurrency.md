# ADR-006: Poller reserved concurrency waits for a quota increase

Date: 2026-11-03
Status: proposed

## Context

The plan gives the poller Lambda a reserved concurrency of 1, so two polls can never run at once (PLAN section 12).

The account's Lambda concurrency quota is 10:

```sh
aws lambda get-account-settings --region eu-west-2 --query AccountLimit
# "ConcurrentExecutions": 10, "UnreservedConcurrentExecutions": 10
```

Lambda refuses any reservation that would take the unreserved pool below its minimum. With a quota of 10, reserving even 1 fails, so a deploy with reserved concurrency 1 would fail at `PutFunctionConcurrency`, after the function was created. A failed apply like that leaves the function tainted, and the next deploy would want to replace it, which the destroy guard blocks.

Every day before the collector deploys is a day of data that can't be recovered, so the collector shouldn't wait for a quota request.

## Decision

- **Reserved concurrency is a variable.** `poller_reserved_concurrency` in `infra/envs/prod` is `-1` (unreserved) for now. Validation only allows `-1` or `1`.
- **Overlap is still prevented.** Without the reservation, the poller still can't overlap or pile up:
  - the code stops a poll after 45 seconds (`POLL_BUDGET_SECONDS`)
  - Scheduler doesn't retry (`maximum_retry_attempts = 0`, 60 s event age)
  - Lambda's own asynchronous retries are off (`maximum_retry_attempts = 0`, 60 s event age)
  - the 60 s timeout ends any run that ignores the budget
- **Switch to 1 after the quota increase.** Request a Lambda concurrent executions quota of 1,000, the usual default (runbook, "Lambda concurrency quota"). Once it is granted, change the variable's default to `1` in a pull request. That is an in-place update, not a replacement.

## Alternatives considered

- **Keep 1 and wait for the quota.** It matches the plan, but blocks the first deploy on an AWS support decision that can take days.
- **Drop the reservation for good.** The budget and no-retry settings make overlap unlikely, but a reservation is the guarantee. It also stops a runaway poller from using the account's whole concurrency pool, which other projects share.

## Consequences

- Until the switch, two polls could in principle overlap, if a run is still going when the next minute's run starts. The 45 s budget makes this rare. If it happens, the run writes extra objects, each with its own `poll_id`. Nothing is lost.
- The throttle alarm stays useful either way. Without a reservation it catches the account limit being hit.
- Revisit when the quota increase is granted.

## Update, 2026-11-08

The quota rose to 1,000 on 2026-11-07 (`aws lambda get-account-settings` returns `"ConcurrentExecutions": 1000`). The default of `poller_reserved_concurrency` is now `1`, as decided above. The change is an in-place `PutFunctionConcurrency` on the poller. Polls can no longer overlap. A minute can still hold two polls, because the post-deploy smoke test adds one (runbook, H10), so minute-level models still treat a minute as one or more polls.
