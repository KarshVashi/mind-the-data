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

Records to write in M0:

- ADR-001: Stack (Lambda + S3 + Athena/Iceberg + dbt + GitHub Actions)
- ADR-002: Polling interval (60 s, with the week-one review as the revisit trigger)
- ADR-003: Raw format (gzipped NDJSON envelopes, failures recorded)
- ADR-004: Multiple statuses per line (worst-status-wins)
