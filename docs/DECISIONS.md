# Decisions inbox

Decisions waiting on the owner. Every PR updates this page. When one is settled, move it to "Settled" with the date and the outcome.

**Classes**

- `auto-apply`: if the owner says nothing by the deadline, the recommended default applies and is recorded under "Settled".
- `never-default`: nothing happens until the owner answers. This class covers security boundaries, IAM or OIDC, money or quotas, metric semantics, publishing and data deletion.

**Deadline** is 72 hours after the item entered this inbox. For items carried over from earlier, that is 2026-11-07, the day this page was created, so 2026-11-10 12:00 UTC.

## Pending

### D1. Accept ADR-006, ADR-007 and ADR-008

- **Context:** all three are `proposed` and already in effect: the poller is unreserved until the quota rises (006), the reference Lambda uses 1024 MB / 300 s (007), and staging merges whole days on a row key (008).
- They have been raised several times without an answer.
- **Default:** set each status to `accepted`. This changes no behaviour. The quota request itself stays an owner step (see STATUS.md).
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D2. Accept ADR-009 (CI roles trust immutable OIDC subjects)

- **Context:** the CI role trust uses the prefix `repo:KarshVashi@41749592/mind-the-data@1442302649`. That has been live since #16, but the ADR is still `proposed`.
- This is an IAM trust boundary, so it needs an explicit yes.
- **Default:** accept as written.
- **Deadline:** 2026-11-10 12:00 UTC to answer. **Class:** `never-default`.

### D3. Station budget: 50 or about 55

- **Context:** `config/stations.yaml` uses all 50 distinct stations (52 calls a minute), and the loader enforces that cap.
- About 55 would add branch ends for wider coverage. That is about 57 calls a minute, still well under the 500 a minute key limit, but it moves PLAN 5.2/5.3.
- **Default:** stay at 50. Revisit in the week-one review with real coverage numbers.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D4. sqlfluff strictness

- **Context:** `.sqlfluff` runs every rule, with the jinja templater, a 100-character lines limit and explicit aliasing. `make check` fails on any violation.
- The question was whether to relax rules, for example layout rules on long CTEs, or leave everything blocking.
- **Default:** keep the current config. Exclude a rule only when it trips, with the reason in a comment in `.sqlfluff`.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D5. Plan checkbox while the plan stays local

- **Context:** the plan is local-only, by the owner's choice, but the project workflow says to tick its checkbox "in the same PR".
- No PR can carry that change.
- **Default:** tick the plan locally when the PR opens, and record the task's state in STATUS.md, which is tracked. PR "Why" sections name the milestone and task by id, for example "M2, task 6".
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D6. dbt unit tests in prod builds

- **Context:** since #18, the daily prod `dbt build` creates and drops temporary Iceberg tables in the prod Glue database for each unit test.
- The alternative is `--exclude-resource-type unit_test` in prod, with unit tests run only against dev.
- **Default:** exclude unit tests from the prod build and run them in dev builds. Unit tests check logic, not prod data, and this keeps prod free of test tables.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D7. Pin dbt-athena to a minor version

- **Context:** `pyproject.toml` allows `dbt-athena-community>=1.9,<2`, and `uv.lock` holds the exact version.
- #18 overrides the adapter's unit-test materialisation and delegates to `dbt.resolve_table_type()`. A minor upgrade could change what it relies on.
- **Default:** narrow it to `>=1.9,<1.10`. Upgrade in a deliberate PR that runs the dev unit tests.
- **Deadline:** 2026-11-10 12:00 UTC. **Class:** `auto-apply`.

### D8. ADR-010: bind OIDC roles to specific workflows (forthcoming)

- **Context:** the CI roles currently trust any workflow on the matching ref or event.
- ADR-010 will propose narrowing trust to named workflow files, for example by `job_workflow_ref`. This needs a bootstrap apply by the owner.
- **Default:** none until the ADR and its PR exist. Then review the trust policy diff and the bootstrap plan.
- **Deadline:** 72 hours after the ADR's PR opens, to answer. **Class:** `never-default`.

## Settled

| Date | Decision | Outcome |
| --- | --- | --- |
| 2026-11-02 | ADR-004: which status wins when a line has several | Highest weight wins. Accepted. |
| 2026-11-03 | Deploy approval on a private repo on GitHub Free | Merging is the approval; deploy on merge (ADR-005). |
| 2026-11-02 | Plan document location | Local-only, not in git. |
