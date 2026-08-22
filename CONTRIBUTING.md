# Contributing to Accretion

Accretion uses a protected integration branch and small, reviewable pull
requests. By contributing, you agree that your work is provided under the
repository's MIT license.

## Development workflow

1. Branch from the latest `develop` using one of the prefixes documented in
   [the branch policy](docs/BRANCH_POLICY.md).
2. Keep the change focused and add or update tests with the implementation.
3. Run the relevant checks locally:

   ```bash
   make check
   ```

4. Use Conventional Commit style for commits and the pull request title, for
   example `feat: add runtime health history` or `fix: preserve event order`.
5. Open a pull request into `develop`, complete the template, and resolve every
   review conversation.
6. Squash-merge after the required CI checks pass.

Release pull requests promote `develop` into `main`. Urgent production fixes
use a `hotfix/` branch from `main` and must be merged back into `develop`.

## Pull request expectations

- Explain the user-visible outcome and the design choice.
- Keep generated files synchronized when their source changes.
- Do not commit credentials, local `.env` files, worktrees, artifacts, or build
  outputs.
- Keep live provider tests opt-in; document any signed-in CLI requirement.
- Update the SDD or runbook when a contract or operational assumption changes.

The project currently permits pull requests without an outside approval so a
solo maintainer is not blocked. Once a second active maintainer is added, the
required approval count should be raised to one on both protected branches.
