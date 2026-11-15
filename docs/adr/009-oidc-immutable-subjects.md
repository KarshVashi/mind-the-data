# ADR-009: CI roles trust GitHub's immutable OIDC subjects

Date: 2026-11-03
Status: accepted (owner, 2026-11-15, D4)

## Context

ADR-005 has the CI roles trust OIDC subjects such as `repo:KarshVashi/mind-the-data:pull_request`. The first pull request plan in CI was refused, and every attempt to assume the role failed.

The repository uses GitHub's immutable subjects. `gh api repos/KarshVashi/mind-the-data/actions/oidc/customization/sub` returns:

```json
{"use_default": true, "use_immutable_subject": true,
 "sub_claim_prefix": "repo:KarshVashi@41749592/mind-the-data@1442302649"}
```

So the subject GitHub actually sends is `repo:KarshVashi@41749592/mind-the-data@1442302649:pull_request`, which carries the owner and repository ids. The `StringEquals` trust condition never matched it.

## Decision

The trust conditions use the full subject prefix from `sub_claim_prefix`, followed by the same contexts as before:

- the plan role: `<prefix>:pull_request`
- the apply role: `<prefix>:ref:refs/heads/main`
- later roles, such as transform: the same pattern

The prefix is a bootstrap variable, `github_subject_prefix`, which replaces `github_repository`.

## Alternatives considered

- **Turn off immutable subjects for the repository.** This would bring back the name-only subjects, but those can be claimed by a different repository that takes over the same name after a rename or deletion.
- **Trust both forms, or use `StringLike` wildcards.** This widens trust for no benefit, since GitHub sends only one form.

## Consequences

- **Rename-proof:** a renamed or re-created repository, even under the same name, cannot assume the roles. The ids would differ.
- **Moving the repository** to another owner, or re-creating it, changes the prefix. Update `github_subject_prefix` and re-apply the bootstrap.
- **Supersedes ADR-005's subject examples only.** ADR-005's decisions are otherwise unchanged.
