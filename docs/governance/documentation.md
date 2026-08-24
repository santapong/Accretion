# Documentation maintenance

Accretion keeps documentation close to the kind of decision it supports. The
folder structure is intentionally shallow, filenames are lowercase and stable,
and `docs/README.md` is the only documentation file at the folder root.

## Ownership by folder

| Folder | Owns | Update when |
|---|---|---|
| `guides/` | Newcomer, developer, frontend, and product walkthroughs | User workflow or public surface changes |
| `runbooks/` | Operational commands, failure handling, and recovery | Runtime behavior or operating procedure changes |
| `research/` | Experiment design, frozen results, acceptance evidence, and decisions | Fixtures, metrics, thresholds, or research interpretation changes |
| `releases/` | Plans, audits, notes, and immutable baseline records by version | Release state or release evidence changes |
| `governance/` | Branch and documentation policy | Repository process changes |
| `sdd/` | Normative versioned system designs and locked forward package | Contract authority changes through the approved SDD process |
| `assets/` | Reviewable diagrams and illustrative images | A primary document's visual model changes |

## File and link rules

- Use lowercase kebab-case filenames outside the versioned SDD package.
- Link relatively so documents work in GitHub and a local checkout.
- Give every SVG a `<title>`, `<desc>`, `role="img"`, and `aria-labelledby`.
- Put operational truth in a runbook, research truth in a report, and release
  truth in an audit. Guides may summarize those sources but do not replace them.
- Keep commands runnable from the repository root unless a document explicitly
  changes directory.
- Add a new document to [the documentation hub](../README.md) in the same pull
  request.

## Experiment and result updates

An experiment change is complete only when the same pull request updates:

1. versioned fixtures and their schema validation;
2. exact fixture SHA-256 values in the detailed report;
3. preregistered thresholds and negative/null cases;
4. deterministic reproduction tests;
5. the relevant acceptance report and decision record;
6. [the consolidated results page](../research/README.md); and
7. release notes or the release audit when the claim is release-facing.

Never replace frozen replay data with live-provider output. Record signed-in
provider/model/version information and redacted artifact hashes separately.

## Release document lifecycle

Each version folder may contain `plan.md`, `audit.md`, `notes.md`, and—only after
the tag exists—`baseline.md`. A plan describes intended work, an audit records
evidence and GO/NO-GO, release notes describe shipped behavior, and the baseline
pins the actual tag object and peeled release commit. Do not precompute a
baseline for an unreleased version.

## Review checklist

- `make docs-check` passes locally and in CI.
- All local Markdown and HTML links resolve.
- SVG files are valid XML and contain accessible metadata.
- No document claims a release, browser check, or live result that was not run.
- Historical milestone counts are labeled historical when later suites grow.
- The project README and documentation hub point to the canonical source rather
  than duplicating detailed tables.
