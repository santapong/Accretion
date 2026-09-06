# Frozen v0.4 baseline

Status: immutable release reference for post-v0.4 compatibility, experiments,
and upgrade work.

The v0.4 baseline records the exact source and frozen evidence shipped on
2026-09-07. It does not freeze `develop`: later work must be additive and must
identify any intentional compatibility change under a new semantic version.

## Canonical identifiers

| Item | Frozen value | Purpose |
|---|---|---|
| Release | [Accretion v0.4.0](https://github.com/santapong/Accretion/releases/tag/v0.4.0) | Published release tag |
| Release tag | `v0.4.0` | Human-facing immutable release name |
| Annotated tag object | `c4067684a7de6f820cdf0703c6d70afc5a29ebae` | Detects a moved or replaced tag object |
| Release commit | `dd1d9f300ba05dc5875b964322c34b47a5288c39` | Canonical v0.4 source, contracts, dependencies, migrations, corpora and release documentation |
| Release tree | `2e59ad70ad36b0934c189ec698bbf7658928b57d` | Proves the promoted `main` tree exactly matched the authorized `develop` tree |
| Authorized `develop` commit | `08a695bcc642fb11bf6b67cbd524ae727610849c` | Final release source before protected promotion (PR #157) |
| Protected bridge head | `e5fda92e8a1fc060c84a7c7c0f1040252c04eddc` | Descendant of the pre-release `main` carrying the exact authorized tree (PR #158) |
| Audited code commit | `cea73eb1eeb6e1cdeb7513de5acff3b085c16542` | The commit `audit.md` measured (M10 closed, #156); the release commit adds versions, lockfiles and the release documents on top of it |
| Previous `main` | `bf5b774eb964252d448b44ec3ea9d6b7b7511213` | Stable branch before this release (v0.3.0) |
| Pre-registration digest | `6b45998b3a9847298ba878a6414211ce38d64c775e5addd587509fdd70cf04ea` | sha256 of `docs/research/v0.4/preregistration.md`, pinned in every router corpus config |

The release commit and the authorized `develop` commit differ because promotion to
`main` is a squash of the protected bridge; the **trees are identical**, which
is the property the policy requires and which
`git diff --exit-code origin/develop origin/main` confirms.

Release PRs: [#157](https://github.com/santapong/Accretion/pull/157) into
`develop`, [#158](https://github.com/santapong/Accretion/pull/158) as the
protected bridge into `main`. All required CI checks — `backend`, `frontend`,
`browser` and `clean-checkout` — passed on both.

## Prior releases are unmoved

| Tag | Tag object | Peeled commit |
|---|---|---|
| `v0.3.0` | `6d20bc6a3b4df4ba2f01920b3717b4cf3c69a2e0` | `bf5b774eb964252d448b44ec3ea9d6b7b7511213` |
| `v0.2.0` | `2c455bac152c971ca85932262ac121c8d847274a` | `de146cd9e1a3e651e066f8dde020c7938cbc1316` |
| `v0.1.0` | `3280e117aadf9ee5f431804dd92bffd2fc80229f` | `6324c8fab1776f0bcc1535f6d6c44fe95588f0e2` |

All three match the values recorded in their own baselines, so no immutable tag
was moved or rewritten by this release.

## Acceptance at the tag

| | Count |
|---|---:|
| Criteria in the four SDDs | 167 |
| Proven by a passing claiming test | 159 |
| Proven by the frontend suite | 5 |
| Proven by a recorded live-provider run (`manual`) | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

Reproduce with `make acceptance`; evaluate the release conditions with
`make release-gate`. Per-criterion status is in
[acceptance-baseline.md](acceptance-baseline.md); the decision and its disclosed
limitations are in [audit.md](audit.md); the locked benchmark's read is recorded in
[the research access log](../../research/v0.4/access-log.jsonl).

## Expiry

The three `manual` criteria — `V01-P0-002`, `V01-P0-004`, `V01-P4-008` — are
backed by [live-acceptance-2026-09-01.md](../v0.3/evidence/live-acceptance-2026-09-01.md)
and **expire on 2027-02-28**. After that date `make acceptance` fails against
this tree until `scripts/live_acceptance.py` is re-run and `last_verified` is
moved. That is deliberate: this baseline records what was true on one day.
