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
| [007](007-reference-lambda-sizing.md) | Reference Lambda gets 1024 MB, 300 s and concurrency 4 | proposed |
| [008](008-staging-merge.md) | Staging merges whole days on a row key, not append | proposed |
| [009](009-oidc-immutable-subjects.md) | CI roles trust the repository's immutable OIDC subject | proposed |
| [010](010-workflow-bound-ci-roles.md) | CI roles bound to their workflow files through `job_workflow_ref` | proposed |
| [011](011-merge-gate.md) | The engineer may merge low-risk pull requests through a gate | proposed |
