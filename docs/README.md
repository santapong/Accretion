# Accretion documentation

This is the single documentation entry point for operators, contributors,
integrators, researchers, and release reviewers. Start with the path that
matches what you want to do; supporting documents are grouped by purpose so the
folder root stays uncluttered.

<img src="assets/project-overview.svg" alt="Accretion project overview from bounded operator intent through deterministic control, isolated runtime execution, and independent verification, with the v0.3.0 release, the preceding v0.2.0 release, and immutable v0.1.0 static-control evidence" width="100%" />

<img src="assets/developer-journey.svg" alt="Six-step developer journey from cloning Accretion through running, observing, verifying, and contributing" width="100%" />

## Choose your path

| Goal | Start here | Continue with |
|---|---|---|
| Understand the project and release status | [Project README](../README.md) | [v0.3 release notes](releases/v0.3/notes.md) |
| Run Accretion locally | [Developer guide](guides/developer.md) | [Showcase](guides/showcase.md) |
| Use or extend the React operator UI | [Frontend guide](guides/frontend.md) | [Developer guide](guides/developer.md), [showcase](guides/showcase.md) |
| Understand the system | [README architecture](../README.md#architecture) | [v0.1 SDD](sdd/Accretion_SDD_v0.1.md) |
| Build a contribution | [Contributing](../CONTRIBUTING.md) | [Branch policy](governance/branch-policy.md) |
| Operate or recover runs | [P0 runtime runbook](runbooks/p0-runtime.md) | [P2 loops](runbooks/p2-feedback-loops.md), [P3 replay](runbooks/p3-recovery.md) |
| Operate P5 dynamic workflows | [P5 runbook](runbooks/p5-dynamic-workflows.md) | [Dynamic benchmark](research/p5/benchmark.md), [decisions](research/p5/decisions.md), [acceptance](research/p5/acceptance.md) |
| Compare P6 candidates | [P6 runbook](runbooks/p6-candidate-search.md) | [Developer showcase](research/p6/showcase.md), [decisions](research/p6/decisions.md), [acceptance](research/p6/acceptance.md) |
| Reuse verified experience | [P7 runbook](runbooks/p7-verified-experience.md) | [Developer showcase](research/p7/showcase.md), [decisions](research/p7/decisions.md), [acceptance](research/p7/acceptance.md) |
| Operate the token broker | [Token broker runbook](runbooks/v03-token-broker.md) | [SDD v0.3](sdd/Accretion_SDD_v0.3.md), [v0.3 backlog](releases/v0.3/backlog.md) |
| Install and govern plugins | [Plugins runbook](runbooks/v03-plugins.md) | [SDD v0.3](sdd/Accretion_SDD_v0.3.md) |
| Administer connections, MCP and identity | [Frontend and administration runbook](runbooks/v03-frontend-admin.md) | [Capability inspector](releases/v0.3/acceptance-baseline.md) |
| Use the research capability set | [Research runbook](runbooks/v03-research.md) | [Experiments and results](research/README.md) |
| Enable enterprise-managed authorization | [Enterprise authorization runbook](runbooks/v03-enterprise-auth.md) | [SDD v0.3 §24.9](sdd/Accretion_SDD_v0.3.md) |
| Check acceptance criteria | [Acceptance baseline](releases/v0.3/acceptance-baseline.md) | [verification policy](acceptance/criteria.toml) |
| Review or reproduce experiments | [Experiments and results](research/README.md) | [ACR-ARCH](research/acr-arch-v0.1.md), [P5](research/p5/benchmark.md), [P6](research/p6/acceptance.md), [P7](research/p7/acceptance.md) |
| Review security | [Security policy](../SECURITY.md) | [Trust-boundary diagram](assets/trust-boundary.svg) |
| Review the current release | [v0.3 release notes](releases/v0.3/notes.md) | [release audit](releases/v0.3/audit.md), [acceptance baseline](releases/v0.3/acceptance-baseline.md), [browser and accessibility evidence](releases/v0.3/browser-a11y-evidence.md) |
| Reproduce the release gate | [Release-hardening runbook](runbooks/v03-release-hardening.md) | [SDD v0.3 §24.8](sdd/Accretion_SDD_v0.3.md), [`scripts/release_gate.py`](../scripts/release_gate.py) |
| Review the previous release | [Frozen v0.2 baseline](releases/v0.2/baseline.md) | [release audit](releases/v0.2/audit.md), [release notes](releases/v0.2/notes.md), [delivery plan](releases/v0.2/plan.md) |
| Plan post-v0.3 work | [v0.3 backlog](releases/v0.3/backlog.md) | [v0.3 SDD](sdd/Accretion_SDD_v0.3.md) |

## Folder map

| Folder | Use it for |
|---|---|
| [`guides/`](guides/) | Local setup, frontend use, extension, and the product showcase |
| [`runbooks/`](runbooks/) | Operating, diagnosing, and recovering P0–P7 and v0.3 M0–M8 behavior |
| [`research/`](research/) | Experiment design, frozen results, acceptance evidence, and decisions |
| [`releases/`](releases/) | Versioned plans, audits, notes, and released baselines |
| [`governance/`](governance/) | Branch workflow and documentation maintenance rules |
| [`sdd/`](sdd/) | Normative versioned system designs and the locked forward-design package |
| [`assets/`](assets/) | Accessible repository-native diagrams and illustrative images |

See [documentation maintenance](governance/documentation.md) for ownership,
naming, experiment-update, and review rules.

## Visual reference

| Diagram | Explains | Primary document |
|---|---|---|
| [Project overview](assets/project-overview.svg) | Product purpose, authority flow, and stable/develop/next release position | [Project README](../README.md) |
| [System architecture](assets/accretion-architecture.svg) | Task-to-runtime and durable telemetry flow | [Project README](../README.md) |
| [Operator frontend map](assets/operator-ui-map.svg) | Eleven UI routes, central live-run evidence, and snapshot/SSE data flow | [Frontend guide](guides/frontend.md) |
| [Developer journey](assets/developer-journey.svg) | First checkout through verified PR | [Developer guide](guides/developer.md) |
| [Feedback lifecycle](assets/accretion-feedback-loop.svg) | Bounded act, observe, verify, repair | [P2 runbook](runbooks/p2-feedback-loops.md) |
| [Checkpoint and replay](assets/checkpoint-replay.svg) | Recovery classification and safe replay | [P3 runbook](runbooks/p3-recovery.md) |
| [Trust boundary](assets/trust-boundary.svg) | Capability, credential, and side-effect authority | [Security policy](../SECURITY.md) |
| [Benchmark pipeline](assets/benchmark-pipeline.svg) | Frozen inputs through reproducible metrics | [ACR-ARCH](research/acr-arch-v0.1.md) |
| [Research results](assets/research-results.svg) | ACR-ARCH control, P5–P7 results, and the separation between frozen, live, and release evidence | [Experiments and results](research/README.md) |
| [Delivery workflow](assets/delivery-workflow.svg) | Protected branches, CI, and releases | [Branch policy](governance/branch-policy.md) |
| [v0.2 roadmap](assets/v02-roadmap.svg) | P5–P7 sequencing and release gates | [v0.2 plan](releases/v0.2/plan.md) |
| [P5 lifecycle](assets/p5-dynamic-workflow.svg) | Proposal, deterministic validation, fallback, activation, and safe replan | [P5 runbook](runbooks/p5-dynamic-workflows.md) |
| [P5 research gate](assets/p5-dynamic-gate.svg) | Static/dynamic cohort uplift, non-inferiority, safety, and fallback | [P5 dynamic benchmark](research/p5/benchmark.md) |
| [P6 search lifecycle](assets/p6-search-lifecycle.svg) | Shared budgets, isolated candidates, fail-closed selection, and crash-safe promotion | [P6 runbook](runbooks/p6-candidate-search.md) |
| [P6 quality/compute curve](assets/p6-quality-compute.svg) | Frozen N=1/2/4 replay result and verified acceptance | [P6 acceptance report](research/p6/acceptance.md) |
| [P7 experience lifecycle](assets/p7-experience-replay.svg) | Materialization, compatibility, frozen selection, fresh-control replay, and revalidation | [P7 runbook](runbooks/p7-verified-experience.md) |
| [P7 transfer gate](assets/p7-transfer-gate.svg) | Frozen uplift, tool use, stale rejection, false accepts, and negative transfer | [P7 acceptance report](research/p7/acceptance.md) |

Every SVG has a title and long description for assistive technology. Technical
diagrams are repository-native so changes can be reviewed as text. The generated
bitmap in the showcase is illustrative and never defines behavior.

## Version authority

- The `v0.3.0` tag defines the current release; its
  [release notes](releases/v0.3/notes.md) and [audit](releases/v0.3/audit.md)
  record what it claims and what it deliberately does not.
- The immutable `v0.2.0` tag and [frozen v0.2 baseline](releases/v0.2/baseline.md)
  remain the previous release. The immutable `v0.1.0` tag remains the
  [frozen static control](releases/v0.1/baseline.md).
- `main` is the stable branch; `develop` integrates the next release.
- `codex/v0.1-local-control-plane` is an unmerged historical prototype, not a
  release branch or source of current contracts.
- Versioned SDDs are normative only for their declared release scope.

## Frontend completion

The React frontend is implemented for every P0–P7 release surface plus the v0.3
M6 administration pages — seventeen routes in all. The deterministic checks cover
97 component tests, generated OpenAPI, ESLint, TypeScript, and production build.
For v0.3.0 rendered browser and accessibility evidence **was** collected: axe-core
4.10.2 reports zero violations across all seventeen routes, which discharges the
v0.2 release exception tracked in
[issue #52](https://github.com/santapong/Accretion/issues/52). See
[browser and accessibility evidence](releases/v0.3/browser-a11y-evidence.md). The
[frontend guide](guides/frontend.md) records the exact routes and evidence model.

## Documentation rules

When behavior changes, update the closest runbook or contract in the same pull
request. Commands must be executable from a clean checkout, generated contracts
must be synchronized, and diagrams must describe implemented behavior or be
clearly labeled as plans. The complete checklist is in
[documentation maintenance](governance/documentation.md).
