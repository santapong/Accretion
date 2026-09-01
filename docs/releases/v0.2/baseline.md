# Frozen v0.2 baseline

Status: immutable release reference for post-v0.2 compatibility, experiments,
and upgrade work.

The v0.2 baseline records the exact source and frozen evidence shipped on
2026-08-24. It does not freeze `develop`: later work must be additive and must
identify any intentional compatibility change under a new semantic version.

## Canonical identifiers

| Item | Frozen value | Purpose |
|---|---|---|
| Release | [Accretion v0.2.0](https://github.com/santapong/Accretion/releases/tag/v0.2.0) | Published, non-draft GitHub release |
| Release tag | `v0.2.0` | Human-facing immutable release name |
| Annotated tag object | `2c455bac152c971ca85932262ac121c8d847274a` | Detects a moved or replaced tag object |
| Release commit | `de146cd9e1a3e651e066f8dde020c7938cbc1316` | Canonical v0.2 source, contracts, dependencies, migrations, and release documentation |
| Release tree | `3828947d0b74f23125193b0553f0a4eb36239460` | Proves the promoted `main` tree exactly matched the authorized `develop` tree |
| Authorized `develop` commit | `7cd9e0a9d90a2d93c1c60907490bd34e98ec5d68` | Final release source before protected promotion |
| Protected bridge head | `bc77001ec7770395481988bb78c0681f293bb7e0` | Descendant of the pre-release `main` with the exact authorized tree |
| Audited code commit | `00220f7713943286b24535549b08ccbeb309637a` | Runtime revision used by the clean-checkout release gate |

The release commit differs from the audited code commit because release and
documentation-only evidence updates followed the runtime audit. Release PR
[#46](https://github.com/santapong/Accretion/pull/46) used the protected bridge
procedure and CI run 164 passed before merge. Post-merge verification confirmed
that the `main`, bridge, and authorized `develop` trees were identical.

## Frozen evidence fingerprints

| Evidence | SHA-256 |
|---|---|
| ACR-ARCH configuration | `c5fcc1a976b05e9770567fa625b0221a183ad8420bf2b1c09fdd1b230ef80466` |
| ACR-ARCH environments | `4740e816ab32f47e42ffbc56b3463ee8f89930e9ce2a09db2bb18dc789164983` |
| ACR-ARCH tasks | `9251bb918912e73a2dade20189f93cc26cd7bc217a0dea03713ef252843b9dd7` |
| ACR-ARCH replay traces | `2f62f87eaf079914d41f47bea57a4dd04ce469d0e54f1d6a38faa6de0dd6f051` |
| P5 configuration | `55678342830491bc20ceea16332b6385c3f6afba3f8fd35fee6342d1260da8de` |
| P5 tasks | `b411b0573d514a496b81b82e25ccee146b66af7fd990187ede6e7ea4c1c399db` |
| P5 replay traces | `77645b41f35430bb886fae558a6ee684664d87b7adcb755c68a92c3db6dd3616` |
| P6 configuration | `9b910c71729ef6bfef5299cb0b8f22f9c75706268ab59185ca17aacc86c8804a` |
| P6 tasks | `11fcdcfb2a698dec4c7aa00af125345cccfb15efb7edf5441cc29530dde4a63f` |
| P6 replay traces | `ffb2085c69931a6af1881ab0f16c44c0bfc19c30b4d77a740290b6faa42e6810` |
| P7 configuration | `42c21144b551edaaaed08d6976807e771da82b055c0455678b9b78c02531be9c` |
| P7 tasks | `4913e7d6d7fc5c676a009ecee328f9e13d225d02b67fdc846ada5caefa3917ff` |
| P7 sources | `968898ea94cb9d1633680ab9a80c4ca92e3b975d5c629069458d851467b713b3` |
| P7 replay traces | `38f1c0b5a1832b8472c63d87ad20a825fd83bca05d3a306d4c087372889ed7a9` |
| Generated TypeScript API schema | `65d4e6fb10c64e1425a1673690a513ac34fc0679a17e54341bb4a387c57d46ff` |
| Redacted balanced live sample | `f378db0cd06fc1e95cfe5527496ea98e12c216a9ebb2d1d59bebdb389c2fe76c` |

Frozen replay results are deterministic fixture claims. The redacted live sample
is separately versioned release evidence and does not mutate replay results.

## What is frozen

- the v0.1 static strategy and validated templates retained as the control;
- opt-in P5 validated dynamic workflows and deterministic static fallback;
- opt-in P6 bounded candidate search, shared budgets, independent ranking, and
  crash-safe promotion;
- opt-in P7 immutable verified experience, compatibility filtering, negative
  knowledge, fresh-control replay, and revalidation;
- migrations `0001` through `0009`, generated API contracts, permission ceilings,
  approval rules, credential isolation, and fail-closed safety behavior; and
- the ACR-ARCH and P5–P7 fixtures and fingerprints listed above.

## Documented release exception

No rendered browser, responsive-layout, keyboard, visible-focus, console, or
automated accessibility PASS is claimed for the tag because the supported
release environment exposed no controllable browser. The maintainer authorized
release with this exception after disclosure. Issue
[#52](https://github.com/santapong/Accretion/issues/52) tracks the post-release
evidence against this immutable tag.

## Verify the reference

From a repository checkout:

```bash
git fetch origin tag v0.2.0
git rev-parse v0.2.0
git rev-parse 'v0.2.0^{}'
git rev-parse 'v0.2.0^{tree}'
sha256sum \
  evals/acr_arch/*.json \
  evals/dynamic_workflow/*.json \
  evals/search/*.json \
  evals/experience/*.json \
  artifacts/release/v0.2.0/acr-arch-live-sample.json \
  apps/ui/src/api/schema.d.ts
```

The Git commands must resolve to the annotated tag object, release commit, and
release tree shown above. Follow the [release notes](notes.md),
[release audit](audit.md), and [experiments index](../../research/README.md) to
reproduce the shipped system and interpret its evidence.
