# Branch policy

<img src="assets/delivery-workflow.svg" alt="Protected delivery workflow from a short-lived branch into develop, through required CI and squash merge, then through a release pull request to main and a semantic version tag" width="100%" />

## Long-lived branches

### `main`

`main` is the stable release branch. It accepts pull requests from `develop`
for planned releases and from `hotfix/*` for urgent fixes. Direct pushes,
deletion, force-pushes, and merge commits are prohibited. Every merge must pass
the backend and frontend CI checks and have all review conversations resolved.

### `develop`

`develop` is the integration branch for the next release. Work reaches it by
pull request from a short-lived branch. It has the same CI, history, deletion,
and force-push protections as `main`.

## Short-lived branches

Create branches from `develop` with a concise kebab-case suffix:

- `feature/` for new behavior
- `fix/` for defects
- `docs/` for documentation-only work
- `refactor/` for behavior-preserving restructuring
- `test/` for test-only work
- `chore/` for maintenance and dependency work

Create `hotfix/` branches from `main`. After a hotfix reaches `main`, merge the
same change back into `develop` through a pull request.

Historical experiment branches, including `codex/v0.1-local-control-plane`, are
not release baselines and must not receive new work. Use the immutable release
tag for v0.1 evaluation and the latest `develop` for current development.

## Merge and release rules

- Pull requests use squash merge so each PR becomes one traceable commit.
- The pull request title follows Conventional Commit style and becomes the
  squash commit subject.
- `develop` is promoted to `main` through a release pull request.
- Releases are tagged from `main` using semantic versions such as `v0.1.0`.
- An administrator may change protection only to resolve an incident; the
  exception must be recorded in an issue and protections restored immediately.

GitHub enforces the mechanical rules. This document defines the intended source
and destination branches that GitHub's protection API cannot express directly.
