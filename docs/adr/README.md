# Architecture decision records

One file per decision, numbered, never edited after acceptance (supersede with a new record instead).

Template:

```
# ADR-NNN: Title

Date: YYYY-MM-DD
Status: proposed | accepted | superseded by ADR-NNN

## Context
What problem, what constraints.

## Decision
What we chose.

## Alternatives considered
What else, and why not.

## Consequences
What gets easier, what gets harder, what to revisit and when.
```

## Index

| ADR | Decision | Status |
| --- | --- | --- |
| [001](001-stack.md) | Stack: Lambda, S3, Athena and Iceberg, dbt, GitHub Actions | accepted |
| [002](002-polling-interval.md) | Poll every 60 s; week-one review is the revisit trigger | accepted |
| [003](003-raw-format.md) | Raw format: gzipped NDJSON envelopes, failures recorded | accepted |
| [004](004-multiple-statuses.md) | Multiple statuses per line: highest weight wins | accepted |
| [005](005-ci-role-ownership.md) | CI roles owned by the bootstrap; merging to main is the deploy approval | accepted |
| [006](006-poller-reserved-concurrency.md) | Poller reserved concurrency waits for a Lambda quota increase | proposed |
| [007](007-reference-lambda-sizing.md) | Reference Lambda gets 1024 MB, 300 s and concurrency 4 | accepted |
| [008](008-staging-merge.md) | Staging merges whole days on a row key, not append | accepted |
| [009](009-oidc-immutable-subjects.md) | CI roles trust the repository's immutable OIDC subject | accepted |
| [010](010-workflow-bound-ci-roles.md) | CI roles bound to their workflow files through `job_workflow_ref` | accepted |
| [011](011-merge-gate.md) | The engineer may merge low-risk pull requests through a gate | accepted |
| [012](012-run-models-rebuild-in-full.md) | Declared-view intermediate models rebuild in full each run | accepted |
| [013](013-poll-per-line-minute.md) | Read the latest poll that lists the line, and only current entries (amends ADR-004) | accepted |
| [014](014-gate-tests-inference-fidelity.md) | The quality gate tests inference fidelity (inferred against trains on the boards); trains run is its own measure (amends PLAN 7.2) | accepted |
| [015](015-divisions-seed-from-two-weeks.md) | Seed the divisions from two complete weeks, provisional until four exist (amends PLAN M6) | accepted |
| [016](016-network-wide-arrivals.md) | Collect arrivals for every station through the per-line endpoint | proposed |
| [017](017-mart-contracts.md) | Enforced dbt contracts on the marts that publishing reads | proposed |
