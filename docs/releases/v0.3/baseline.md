# Frozen v0.3 baseline

Status: immutable release reference for post-v0.3 compatibility, experiments,
and upgrade work.

The v0.3 baseline records the exact source and frozen evidence shipped on
2026-09-01. It does not freeze `develop`: later work must be additive and must
identify any intentional compatibility change under a new semantic version.

## Canonical identifiers

| Item | Frozen value | Purpose |
|---|---|---|
| Release | [Accretion v0.3.0](https://github.com/santapong/Accretion/releases/tag/v0.3.0) | Published release tag |
| Release tag | `v0.3.0` | Human-facing immutable release name |
| Annotated tag object | `6d20bc6a3b4df4ba2f01920b3717b4cf3c69a2e0` | Detects a moved or replaced tag object |
| Release commit | `bf5b774eb964252d448b44ec3ea9d6b7b7511213` | Canonical v0.3 source, contracts, dependencies, migrations, and release documentation |
| Release tree | `902c5c75aa899ecb2306cf26696bbccb867fc797` | Proves the promoted `main` tree exactly matched the authorized `develop` tree |
| Authorized `develop` commit | `0bf1d747eb9428efe13d8e71b13e3866e9cebb92` | Final release source before protected promotion (PR #102) |
| Protected bridge head | `b849ca0846b12977042d72cbc5a5841504ca7fbd` | Descendant of the pre-release `main` carrying the exact authorized tree (PR #103) |
| Audited code commit | `1f03dec5538ca18973e3a18f1e4f7ca03f311dd1` | Branch tip audited in `audit.md` before promotion |
| Previous `main` | `de146cd9e1a3e651e066f8dde020c7938cbc1316` | Stable branch before this release (v0.2.0) |

The release commit and the audited code commit differ because promotion to
`main` is a squash of the protected bridge; the **trees are identical**, which
is the property the policy requires and which
`git diff --exit-code origin/develop origin/main` confirms.

Release PRs: [#102](https://github.com/santapong/Accretion/pull/102) into
`develop`, [#103](https://github.com/santapong/Accretion/pull/103) as the
protected bridge into `main`. All required CI checks — `backend`, `frontend` and
the new `clean-checkout` job — passed on both, and again on `main` after merge.

## Prior releases are unmoved

| Tag | Tag object | Peeled commit |
|---|---|---|
| `v0.2.0` | `2c455bac152c971ca85932262ac121c8d847274a` | `de146cd9e1a3e651e066f8dde020c7938cbc1316` |
| `v0.1.0` | `3280e117aadf9ee5f431804dd92bffd2fc80229f` | `6324c8fab1776f0bcc1535f6d6c44fe95588f0e2` |

Both match the values recorded in their own baselines, so neither immutable tag
was moved or rewritten by this release.

## Acceptance at the tag

| | Count |
|---|---:|
| Criteria in the three SDDs | 117 |
| Proven by a passing claiming test | 111 |
| Proven by the frontend suite | 3 |
| Proven by a recorded live-provider run (`manual`) | 3 |
| Uncovered | 0 |
| **Unmet MUST** | **0** |

Reproduce with `make acceptance`; evaluate the release conditions with
`make release-gate`. Per-criterion status is in
[acceptance-baseline.md](acceptance-baseline.md); the decision and its disclosed
limitations are in [audit.md](audit.md).

## Expiry

The three `manual` criteria — `V01-P0-002`, `V01-P0-004`, `V01-P4-008` — are
backed by [live-acceptance-2026-09-01.md](evidence/live-acceptance-2026-09-01.md)
and **expire on 2027-02-28**. After that date `make acceptance` fails against
this tree until `scripts/live_acceptance.py` is re-run and `last_verified` is
moved. That is deliberate: this baseline records what was true on one day.
