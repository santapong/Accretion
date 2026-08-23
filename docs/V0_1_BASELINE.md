# Frozen v0.1 baseline

Status: frozen release reference for v0.2 compatibility and experiments.

The v0.1 baseline is the immutable, reproducible control treatment that later
Accretion releases must preserve and compare against. It is not a freeze on the
`develop` branch: development continues through additive, versioned changes.

## Canonical identifiers

| Item | Frozen value | Purpose |
|---|---|---|
| Release tag | `v0.1.0` | Human-facing immutable release name |
| Annotated tag object | `3280e117aadf9ee5f431804dd92bffd2fc80229f` | Detects a moved or replaced tag object |
| Release commit | `6324c8fab1776f0bcc1535f6d6c44fe95588f0e2` | Canonical v0.1 source, contracts, dependencies, and migrations |
| Audited candidate | `934ea75cf06be820ac6fdd0946e3203982c779c4` | Code revision used by the clean-checkout release gate |
| P5 integration base | `bb249f5b2b2a273606ba81d61be5a8f20010f9a4` | `develop` snapshot from which the P5 implementation branch started |

The audited candidate and release commit differ because the promotion included
release documentation. The [release audit](V0_1_RELEASE_AUDIT.md) records that no
code change was allowed between the audited candidate and release promotion.

## What is frozen

- the v0.1 static strategy behavior and validated workflow templates;
- v0.1 API and identifier compatibility, runtime protocol, and durable audit
  references;
- migrations `0001` through `0006` as represented by the release tag;
- permission ceilings, approval rules, credential isolation, independent
  verification, bounded budgets, and fail-closed behavior;
- the ACR-ARCH task corpus, replay evidence, configuration, and published
  checksums in the release audit.

For v0.2 research, this release remains the static control treatment. P5 may
propose dynamic graphs only behind opt-in gates and must retain the validated
v0.1 strategy as its fallback. Later phases must report results against the same
baseline, including negative and null results.

## What is not frozen

- `develop`, which continues to integrate the next release;
- additive `/api/v2` contracts, new migrations, UI inspection surfaces, or
  other versioned v0.2 behavior;
- fixes delivered under a new semantic version. The `v0.1.0` tag itself must
  never be moved to include them.

The historical `codex/v0.1-local-control-plane` branch is a prototype and is not
part of this baseline.

## Verify the reference

From a repository checkout:

```bash
git fetch origin tag v0.1.0
git rev-parse v0.1.0
git rev-parse 'v0.1.0^{}'
```

The commands must resolve to the annotated tag object and release commit shown
above. To reproduce the shipped system, create a detached checkout from the tag
and follow the [v0.1 release notes](V0_1_RELEASE_NOTES.md) and
[release audit](V0_1_RELEASE_AUDIT.md).
