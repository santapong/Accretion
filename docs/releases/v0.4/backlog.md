# v0.4 prioritized backlog

Status: **M1, M3, M4 and M5 delivered; M2 locally verified, awaiting remote review.** The normative contract is [SDD v0.4](../../sdd/Accretion_SDD_v0.4.md);
its §19 orders the milestones and its §20 owns the criteria. This ledger records status only.

## Delivery order

| Priority | Milestone | Owns (SDD §20) | Status |
|---:|---|---|---|
| 1 | M0 contract and feature freeze | none (ADR-052) | delivered (#123) ([plan](m0-plan.md), [freeze record](m0-freeze.md)) |
| 2 | M1 compatibility engine | 005-008 | delivered ([plan](m1-plan.md)) |
| 3 | M2 hierarchical deterministic selector | 001, 002, 004, 009-015, 022 | locally verified; remote review pending ([evidence](m2-plan.md), [runbook](m2-runbook.md)) |
| 4 | M3 experience and feedback pipeline | 003, 023-034 | delivered ([plan](m3-plan.md), [runbook](../../runbooks/v04-feedback.md)) |
| 5 | M4 offline ranker and calibration | 016 | delivered ([plan](m4-plan.md)); activation remains disabled |
| 6 | M5 project adapter and cold start | 021 | delivered ([plan](m5-plan.md)) |
| 7 | M6 shadow routing | 017, 041 | not started |
| 8 | M7 guarded bandit | 018-020 | not started |
| 9 | M8 promotion and rollback | 035-039, 042 | not started |
| 10 | M9 Experiment Studio | 040, 043, 044 | not started |
| 11 | M10 research benchmark integration | 045-050 | not started |

No milestone may enable online exploration before the M0-M6 gates pass (SDD v0.4 §19).

## Carried from v0.3

The items the v0.3 release deliberately deferred are listed under "M7 deferrals" and the
"Deferred to v0.4" notes in the [v0.3 backlog](../v0.3/backlog.md): workspace-shared and
`SERVICE_ACCOUNT` enterprise authorization, session enumeration in the identity page, real
identity-provider interoperability as an expiring manual criterion, and the token-exchange egress
allowlist. None is a v0.4 acceptance criterion; each is scheduled when a v0.4 milestone touches
its surface, and none is added to the M0 freeze. Also carried: the read-boundary schema upcaster (registry §20.5) scheduled for M8 (ADR-057).

## Recorded during M0

**Converge the seven pre-v0.4 JSON digest sites on `contracts/canonical.py` — scheduled for M8,
alongside the read-boundary upcaster.** ADR-056 says canonical serialization is "implemented once
in `contracts/canonical.py`", and from M0 every *new* contract obeys that. Seven older call sites
still hand-roll their own `json.dumps(..., sort_keys=True, separators=(",", ":"))`:
`governance.py:271` (the capability idempotency digest), `governance.py:1023` (the
`accretion-core-governance@1.0.0` manifest checksum), `templates.py:73`, `mcp/manager.py:653`,
`orchestration/validator.py:245`, `experience/embedding.py:46` and `live_sample.py:158`. All but
`embedding.py` leave `ensure_ascii` at its default `True`, so for any payload containing non-ASCII
they emit `\uXXXX` escapes and therefore different bytes — and a different digest — from
`canonical_json`.

M0 deliberately leaves all seven alone, and the reason is the whole point of recording this rather
than letting a later milestone rediscover it by breaking CI. The digest at `governance.py:1023` is
already persisted: it is the `checksum` on the immutable `plugins` row for
`accretion-core-governance@1.0.0`, and `upsert_plugin` rejects any drift for an existing
`(plugin_id, version)` (`store.py:1436` and `:3534`). Converging that site would change the digest
for any manifest carrying non-ASCII content and make the next `seed_governance` fail with
`ValueError: plugin accretion-core-governance@1.0.0 is immutable` on every deployment that already
ran. The same argument holds in weaker form for the idempotency and validator digests, which are
compared against values earlier releases wrote. Convergence is therefore not a refactor but a
rehash-and-migrate story, and it belongs in the milestone that already owns a read-boundary
upcaster (ADR-057): **M8**.

Until then the rule is narrow and enforceable: new v0.4 contract hashing goes through
`accretion.contracts.canonical`, the seven sites stay byte-frozen, and no code compares a digest
produced by one against a digest produced by the other.

### Outcome (M8, PR `digests`) — three converged, four byte-frozen

`tests/test_v04_m8_digests.py` measured all seven against the payloads this repository already
commits, and ran green **before** any site was touched: every one of the seven is byte-identical
under `canonical_json` on every committed payload. That is not sufficient to converge, because a
committed payload is not the payload domain. The deciding question per site is whether a non-ASCII
value is *reachable at runtime* and whether the digest is *persisted and compared*. Three sites
answer no and were converged; four answer yes and now share one copy of the old expression in the
new `src/accretion/digests.py` (`legacy_json_digest`), so there is one place left to change when
the read-boundary upcaster (ADR-057) can finally carry the rehash.

| Site | Outcome | Reason |
|---|---|---|
| `experience/embedding.py:46` `canonical_digest` | **converged** | Already passed `ensure_ascii=False`, so it agrees with `canonical_json` byte for byte on every payload *both* functions accept (there is no payload they both accept and serialize differently); `canonical_json` additionally refuses non-string object keys and non-finite floats, which now raise `CanonicalizationError` instead of digesting invalid JSON. Its persisted digests (`ExperienceEmbedding.input_digest`, segment `content_digest`, the three bundled plugin manifest digests) cannot move. |
| `governance.py:1023` `seed_governance` | **converged**, no version bump | The payload is four code literals — plugin id, version, two built-in capability ids, one built-in skill id — so the domain is closed and entirely ASCII and the checksum is the same constant either way (`3328cb72…`, pinned in the test). Re-seeding a store that already holds the 1.0.0 row is proven idempotent. |
| `live_sample.py:158` | **converged** | Not a digest: it serializes the expected artifact into the prompt that asks a provider to write `result.json`, and `verify_artifact` compares *parsed* objects, so the escaping cannot change a verdict or the recorded `artifact_sha256`. All ten frozen assignments serialize identically either way. |
| `governance.py:271` `approval_binding` | **legacy** | `CapabilityRequest.arguments` is arbitrary caller-supplied JSON and the digest becomes an approval's `native_request_id`. |
| `templates.py:73` `compute_template_checksum` | **legacy** | A template body carries free text, and `orchestration/materialize.py` builds one from a planner proposal. The checksum is persisted and re-verified at load and at run start. |
| `mcp/manager.py:653` | **legacy** | The snapshot digest covers `server_info`, tool descriptions, resource names and prompt descriptions supplied by a *remote* server — the likeliest non-ASCII payload here — and is persisted as `McpDiscoverySnapshot.content_sha256`. |
| `orchestration/validator.py:245` `normalized_hash` | **legacy** | Covers `DynamicWorkflowNodeSpec.objective`, four thousand characters of free planner text, persisted as `GraphValidationResult.normalized_graph_hash`. |

No persisted digest moved: the built-in governance plugin checksum, the five built-in template
checksums, the fragment-planner graph hash, the MCP discovery snapshot digest and the ASCII
approval binding are all pinned as literal hex in the test, and each of the four legacy sites has a
non-ASCII probe asserting the *site itself* still returns the legacy bytes. The narrow rule above
survives unchanged for the four that remain.

## Recorded during M1

Three decisions M1 had to make that the SDD does not settle. Each is recorded here rather
than in the SDD because none of them changes the design; each records *which* of two readings
of the design the code took, so that a later milestone reading the same paragraph does not
take the other one.

**ADR4-M1-001 — the workspace of a run is derived from its principal, not from its project.**
Every registry §3 record is workspace-scoped and a `Run` is not: it carries a `project_id` and
a `principal_id`, and `Project` has no workspace column at all. So a compatibility decision
about a run has to get its `workspace_id` from somewhere, and there were only two honest
candidates. `accretion.routing.identity.workspace_for_run` takes the first workspace of
`list_workspaces_for_principal(run.principal_id)` — that method sorts, so a principal in
several workspaces gets a deterministic answer rather than whichever row the database
returned first — and falls back to `identity.LOCAL_WORKSPACE_ID` for a run with no principal
or no membership, which is exactly what the identity service seeds for single-user local
operation (OQ3-17).

The rejected alternative was adding a workspace column to `projects`. It is the better
long-term model and it is a migration, a backfill and a tenancy change, none of which belongs
in the milestone that owns the compatibility engine. M2 persists receipts against this
derivation; if the column is ever added, `workspace_for_run` is the one place that changes.

**ADR4-M1-002 — the twelve v0.4 `EventType` members were pre-declared in M1.1, all at once.**
M1.1 added every routing, feedback and router event the release will emit, including the ones
no code emits yet. Declaring them one milestone at a time would have been the smaller diff and
the worse trade: `EventType` is exported into `openapi.json` and into
`apps/ui/src/api/schema.d.ts`, and CI fails on `git diff --exit-code` for both, so each
milestone that added a member would regenerate two committed artifacts and every reviewer
would have to decide again whether the churn was benign. Declaring them once means no later
v0.4 PR touches either file for an event, and an unused member is visible as unused rather
than as missing.

**ADR4-M1-003 — the reason catalogue is versioned by `RULE_VERSION`, and M1.2 did not bump
it.** `routing/reasons.py` states the rule: adding a code, removing one, or changing what an
existing one means all bump `RULE_VERSION`, because a persisted decision is only explicable
against the exact rules that produced it and "the rules changed" must be a visible event. M1.1
therefore pre-declared every code it expected M1.2's gates to emit, so that M1.2 would not
have to bump a version whose rules had not changed.

It missed one. The pre-declaration enumerated the codes the gates *refuse* with and not the
one they *defer* with, so M1.2 added `APPROVAL_REQUIRED` — the code that says the policy
engine answered `REQUIRE_APPROVAL` and routing does not pre-approve. `RULE_VERSION` stays
`compat-rules/1` anyway, and the reason is narrow rather than convenient: the version exists
so that a persisted decision stays explicable, and no decision under `compat-rules/1` exists
outside this repository's tests. M1 is the first milestone that emits one at all and it has
not shipped, so there is no reader pointing at the old catalogue to mislead. Once v0.4 ships,
the rule applies unmodified: the next code bumps the version.
## Recorded during M4

Three open questions closed while building the offline ranker. Each is recorded here rather than in
the SDD, because the SDD states the question and these are the answers this release gave.

**ADR4-M4-001 (OQ-401, outcome model) — an in-repo gradient-boosted ranker, and no new
dependency.** The default was "gradient-boosted baseline first", with LightGBM or scikit-learn's
`HistGradientBoosting` to be chosen by a dependency-weight check. The check ran and both lost.
`routing/gbdt.py` is about six hundred lines of exact arithmetic and it buys three things a
library would have cost: **determinism that is ours** (the same seed produces byte-identical trees
on every machine and Python build, which is what makes `artifact_digest` a stable identity rather
than a description of one machine's floating point), **a serialization format we own** (the
artefact is canonical JSON that a reader can diff, not a pickle or a binary blob that a version
bump can stop reading), and **no new wheel on the runtime image** for a workload measured in
hundreds of rows and minutes. At low thousands of rows a compiled learner behind the existing
`Learner` protocol starts to earn its weight; the protocol is why swapping it is a PR and not a
migration. R1 agrees on the shape: a small tree ranker is the right cold-start model.

**ADR4-M4-002 (OQ-405, verified-success lower bound) — conformal is primary, the bootstrap is the
sensitivity check, and both travel in the report.** `conformal_quantile` is taken over *projects*,
not rows, so a hundred rows from one project buy exactly as much confidence as five do; the
quantile is fitted on the calibration split and its coverage is measured on the holdout, which is
the only arrangement in which split conformal means anything. The bootstrap remains as the
project-level ECE interval (B=200) in the same `CalibrationReport`, so a reader can see both. Two
consequences are stated out loud rather than hidden: with fewer than `1/α − 1` calibration
projects there is no finite quantile at all and the function returns 1.0, collapsing every lower
bound to zero — vacuous and visibly so; and `DistributionEstimate.method` distinguishes the
conformal success bounds from the magnitude heads' bag band, which is ensemble disagreement and
not a coverage statement.

**ADR4-M4-003 (OQ-415, drift window) — revalidate on a behaviourally material version change, and
record the serving configuration inside the window.** The default stands, tightened by R7: a
provider version alone does not identify what served a rollout, because quantization, temperature
and the sampling seed move quality and cost without moving any field the router selected. The
freeze delta's `ServingWindow` (ADR-060) carries those knobs as `serving_labels`, and a snapshot's
`provider_version_boundaries` names the version window each cut was taken under, so a candidate
trained across a material serving change is visible as two boundaries rather than as unexplained
drift. M4 does not yet *detect* materiality; it makes the evidence for that detection recordable,
and M6's branched rollouts are the first consumer.

## Recorded during M5

Three open questions closed while building the cold-start scorer. Each is recorded here rather
than in the SDD, because the SDD states the question and these are the answers this release gave.

**ADR4-M5-001 (OQ-406, project-adapter form) — the residual corrects the prior's *logit*, and the
same delta moves the mean and the lower bound.** The default was "regularised residual or
calibration layer", and the residual won for the reason `routing/adapter.py` opens with: a
project-scoped model fitted on a project's first handful of runs is a model fitted on noise, and
it is at its most confident exactly when it knows least. Parameterising the adapter as a two-number
affine residual on the prior's logit makes the failure mode benign — at zero coefficients it is the
identity function. What M5.2 adds is the serving rule: `ColdStartScorer._adapt` applies the fitted
delta to `DistributionEstimate.mean` **and** to `lower_bound`, not to the mean alone. Shifting only
the mean would let a project's own history raise a candidate's expected value while leaving the
number §9.5's safe set is defined on untouched, which is a way of passing the gate by not being
measured by it. `n_project` is the project's in-domain count at serving time (`n_same_signature`)
and is deliberately not `artifact.n_fit`: an adapter fitted last week on forty outcomes must not
claim the authority of the ninety the project has now, nor keep the authority of forty if the
eligible evidence has since shrunk.

**ADR4-M5-002 (OQ-408, cross-domain prior cap) — 0.15, on the mean only, with the bound outside the
function's signature.** The default was "a small fixed cap, tuned only on validation, with a
property test proving the cap alone cannot lift an LCB over τ". The cap is fixed at 0.15 and is
*not* tuned: a cap that moved with the data would be a cap the data could raise, and §9.4's
sentence is a bound rather than a hyperparameter. It is reached through a shrinkage weight
`min(0.15, n_cross / (n_cross + 20))`, so the first out-of-domain record does not weigh as much as
the hundredth, and `k` can only change how fast the weight climbs *toward* the cap. The load-bearing
part is structural rather than numeric: `coldstart.cross_domain_prior` takes the mean and does not
take the bound, so no amount of cross-domain evidence can reach the quantity the §9.5 gate reads.
The property test is `AC4-M5-021`'s, and it pins 0.15 as a literal rather than importing the module
constant — importing it would make raising the cap invisible to the test that exists to catch it.

**ADR4-M5-003 (vocabulary pinning) — the scorer refuses a prior fitted under another token table.**
Not an SDD open question but a gap M4 left: `RankerArtifact` records `feature_schema_version` and
`n_features` but no vocabulary, while `features.Vocabulary` decides which column index a model id
or an adapter version lands in. Two models with the same schema version and different vocabularies
therefore mean different things by the same two columns, and nothing in the artefact can tell them
apart. The only written record is the training snapshot's `vocab_digest` label, so
`ColdStartScorer` reads it and degrades to the deterministic baseline under
`degraded=VOCABULARY_MISMATCH` when it does not match the table the scorer featurizes under. The
alternative — predicting anyway — is silent at every other layer and would present as a merely
worse model rather than as a mismatched one. Moving the vocabulary into `RankerArtifact` is the
cleaner fix and is deferred: it changes an artefact digest, which is a §7.12 identity change and
belongs to a milestone that owns retraining.

## Recorded during M9

Four decisions the operator-UI milestone had to make before it could add a single element to
the run page. None changes the design; each records which of two readings the code took.

**ADR4-M9-001 — a structural-change waiver for the computed-style diff, scoped to one route and
one PR.** `e2e/style-diff.spec.ts` compares the branch build to the merge-base build by aligning
two element captures **by index**, so ANY added element makes the two a different shape and the
gate reports "the two builds rendered different DOM after 3 attempts". That is correct for a
stylesheet port, which is what the gate was built for, and wrong for every PR after it: M9 exists
to add screens. The three ways out were to delete the gate, to set `STYLE_DIFF_SKIP` on PRs that
add markup, or to name the exemption. The first two are the same thing with different amounts of
paperwork — a required check that reports green while measuring nothing.

So `RouteUnderTest` gains an optional `structuralChange: { pr, reason }`, and
`styleDiff.ts` gains `obligationsFor` and `compareCaptures`, which read **that route's** waiver
and no other. A waived route skips `fingerprintsEqual` and `diffStyles` and nothing else: the
element floor, the interaction pass's focus floor and the whole a11y gate still run on it, and
the sweep's log line prints `STRUCTURAL CHANGE WAIVED by <pr>` so "0 differences" can never be
read as "compared and identical". The floor is what makes this safe — with the structural
comparison off it is the only remaining evidence that the branch rendered the page at all, so a
waived route that 500s still fails.

Two mutations in `e2e/styleDiff.test.ts` are the evidence that the exemption stops where it is
supposed to, and both were run: a waiver held in module state instead of read per route fails
"a waiver on one route does not silence a style difference on another", and
`enforceFloor: !route.structuralChange` fails "a waived route still enforces the element floor".
Neither failure is visible from a Playwright run, because a leaked waiver prints the same
"0 differences" a healthy branch does.

The waiver is deliberately not durable. It names the PR that earned it, so the next PR that
touches the route deletes it and declares its own; that is the only thing that stops the busiest
route in the app from acquiring a permanent exemption.

**ADR4-M9-002 — the run page discovers routing receipts from the audit it already fetches; no
"receipts of a run" route is added.** M2 serves a receipt and its candidates by receipt id and
nothing that lists them, and the §17.1 panel needs the list. Adding `GET
/api/v1/runs/{run_id}/routing-decisions` was the obvious answer and is not needed: the audit
already carries the mapping twice over. `services/run_manager.py` stamps every
`RUNTIME_CALL_STARTED` of a routed call with `payload.routing_receipt_id` and the projection's
own `node_id`, so one event names a node AND a receipt; `routing/service.py` appends
`ROUTING_DECISION_CREATED` with `payload.receipt_id` and `causation_id =
receipt.contract_id`, which is how a decision that never dispatched — a `HUMAN_REVIEW_REQUIRED`
waiting for a person, the receipt an operator most needs — is still discoverable.

`apps/ui/src/routingIndex.ts` is that projection, pure and importing no API client, in the shape
`runBadges.ts` established. The cost of the alternative is not the endpoint, it is the second
source of truth: a list route would have its own ordering, its own pagination and its own
authorization, and could disagree with the log the same page renders below it.

**ADR4-M9-003 — new markup styles reuse the pinned sheet's class vocabulary; no rule is appended
to `theme.css`.** The M9 PR5c plan says new styles go in `@layer components` in `theme.css`.
`e2e/cssPort.test.ts` does not allow it, and this was measured rather than assumed: appending one
rule fails three of its cases — "every pinned rule survives exactly once across the live
stylesheets" (`invented`, plus the `liveKeys.length === pinnedKeys.length` equality), "theme.css
reordered its rules", and "a rule in `@layer components` is not a byte-verbatim slice of the
pinned sheet". That is not a bug in the test. With `styles.css` deleted, union equality against
`fixtures/styles.pre-pr5.css` **is** the completeness proof of the whole port, and its strength
comes precisely from admitting nothing that was not in the pre-migration sheet.

M9a therefore builds the routing panel from classes the pinned sheet already declares —
`.dynamic-inspector`, `.proposal-inspector`, `.router-evidence`, `.router-features`,
`.router-fallback`, `.graph-diff-identities`, `.replan-control`, `.dynamic-metrics`, `.quiet`,
`.form-status`, `.secondary-button` and the `.pill-*` states — which is also why it looks like
the inspector it sits beside rather than like a new design. It costs nothing in the CSS budget
and it is not a workaround for a check: it is the check being right.

The follow-up is real and belongs to whichever M9 PR first needs a rule that does not exist yet.
`cssPort.test.ts` needs a post-port additions ledger — a named list of rules that are new rather
than ported, subtracted from `invented` and from the ordering pin, exactly as the waiver above is
subtracted from the structural comparison — so that "every pinned rule survives exactly once"
keeps its current strength while new rules stop being indistinguishable from edited ones. That is
a change to a completeness proof and it is not something to do incidentally inside a feature PR.

**ADR4-M9-004 — the §17.2 comparison joins a workspace-wide shadow report to one run in the
browser; no run-scoped shadow route is added, and the aggregate is never recomputed per run.**
`GET /api/v1/shadow-policies/{version_id}/report` is a report about one *policy*, over every
decision the stage scored, and the run page needs one run's slice of it. Two answers were
available. The first was a new route — a `run_id` filter on the report, or
`GET /api/v1/runs/{run_id}/shadow-pairs` — which would have to recompute `mean_delta` and
`delta_lcb` over the filtered rows to stay coherent, and that is the trap: a bootstrap interval
over one run's two or three pairs clears or misses any floor at random, and it would sit on the
same screen as, and disagree with, the interval M8.2 gates the promotion on. `shadow_report`
refuses an empty decision list for exactly this reason, and a per-run route would have had to
un-refuse it.

So the join is done in the client, over reads that already exist: `routingIndex.ts` gives the
run's receipt ids from the audit the page already fetches (ADR4-M9-002), and `ShadowPair` carries
`executed_receipt_id` and `shadow_receipt_id` — which is why `routing/shadow.py` lists incomplete
decisions rather than filtering them, and says so in its own docstring. `ShadowComparison.tsx`
keeps a pair when either half names one of this run's receipts, renders those pairs, and renders
the five aggregate numbers **unchanged and labelled as the stage's**, beside a line saying how
many of the stage's pairs came from here. One number, computed once, in the one place it is
defined.

Two costs are accepted with it. The stage list is `GET /api/v1/router-models` filtered in the
browser to `SHADOW` and to versions whose `project_id` is this run's or null, because that route
offers a `project_id` parameter and no status one — and passing `project_id` would also drop the
workspace-scoped stages, which score every project's runs.

The second cost is a genuine gap, recorded rather than worked around. The workspace comes from
`api.me().memberships[0]`, which is the shell's own rule and is a guess: **no read exposes a
run's workspace.** `GET /api/v1/runs/{run_id}` returns `Run`, which carries `project_id` and no
`workspace_id`, and `GET /api/v1/projects/{project_id}` returns `Project`, which carries
`project_id`, `name` and `repository_path` and no `workspace_id` either. For an operator with one
membership — every current deployment — the guess is right. For an operator in two workspaces
whose run belongs to the second, the panel lists the first workspace's stages and shows an empty
comparison, which is the safe failure but still a wrong one. Adding `workspace_id` to `Project`
is the small fix; adding it to `Run` is the direct one. Either is a contract change and belongs
to the milestone that owns those contracts, not to a UI PR.

## Recorded during M6

**ADR4-M6-001 (R7, ADR-060; how a shadow recommendation is scored) — branch the run, never replay
it.** The cheap option was to replay the executed trajectory against the shadow configuration and
read off what changed. It answers the wrong question. A trajectory is a record of what *one*
configuration did, and a different configuration diverges from it at the second turn, so a replay
measures how well the shadow imitates the executed run rather than how well it does the node's
work — and it measures that most favourably for configurations most similar to the one already
running, which is the one bias a promotion decision cannot afford. `BranchedRolloutExecutor`
therefore forks the run: `WorktreeManager.acquire_candidate` gives each arm a fresh sandbox at the
run's base revision, each arm opens its own session under its own model id, and both arms are
graded by the frozen verification spec the node was routed against. The CONTROL arm is the
executed configuration *re-run in a fork of its own* rather than the live node, because the live
node started from a different workspace state and the difference would be attributed to the
router. The price is real — a fork is a second execution and a recurring cost — so it is paid
under three gates rather than accepted: `LOW_DIGITAL` risk only, `WORKTREE` isolation only, a
digest-sampled `fork_fraction`, and the registered `ShadowBudget` charged per policy per UTC day.
A refusal appends a `router.shadow.rollout-skipped` event naming its reason; a fork that raises is
a log line and never a failed run.

**ADR4-M6-002 (OQ-409, how much paired evidence a promotion needs) — an interim floor, stated as a
constant, pending §21.** OQ-409 is open: the SDD does not fix the number of paired runs a
promotion may be granted on, and it will not be fixed by a milestone that has never run the
experiment. The interim rule this repository operates under is **at least nine complete paired
runs per configuration per node class**, which is the smallest number at which the
decision-clustered bootstrap is resampling clusters rather than describing its own resampling.
It is not implemented as a per-cohort assertion, because M6 has no cohort vocabulary and inventing
one would be the duplicate source of truth registry §21 forbids; it is implemented as
`shadow.DEFAULT_MIN_PAIRED_RUNS = 30`, a workspace-wide floor on *complete* pairs that a
three-configuration, one-node-class stage clears at exactly the interim rate.
`ShadowReportConfig.min_paired_runs` carries it so that a report and the promotion it supports
state the bar they applied, and `shadow_gate` refuses a report below the count *before* consulting
its interval — a lower bound computed from four pairs can clear any floor, and reporting it as a
pass would make the count gate decorative. The real number is a power analysis over the first
stage's variance and belongs to whichever milestone closes OQ-409; until then the constant is the
place to change it, and both consumers read it from there.
## Recorded during M3

Four decisions M3 had to make that the SDD does not settle, and one earlier decision this
milestone supersedes.

**ADR4-M3-001 (OQ-407, attribution) — the dependency heuristic is v1 and is versioned so it can
be replaced without rewriting history.** §9.6 asks for credit assignment and names no method.
`feedback/attribution.py` derives credit from the graph's own dependency structure and the
node's local verdict, stamps `method_version` on every `AttributionSummary`, and writes a
*revision* when it recomputes rather than editing the row it recomputed. The heuristic will be
wrong for some graphs; the design decision is that being wrong is recoverable, because the
method that was wrong is named on every record it produced and a better one appends rather than
overwrites. A learned attributor is a later milestone's work and needs no schema change.

**ADR4-M3-002 (OQ-414, retention) — experience records inherit the P7 experience's retention and
declare none of their own.** §7.10's projection is keyed by the experience it projects (ADR-054
b), so a record whose P7 row has been retracted or aged out is already ineligible by
dereference. Giving the projection its own `retention_class` would have created two clocks over
one fact, and the shorter of the two would silently decide — which is the failure mode registry
§21 exists to prevent. The consequence is deliberate: retention policy is set once, on the P7
experience, and the routing projection cannot outlive its subject.

**ADR4-M3-003 (materiality) — a material conflict is PASS *versus* FAIL on one REQUIRED claim,
and it blocks acceptance rather than deciding it.** The alternative readings were "any
disagreement blocks" and "the worst verdict wins". The first deadlocks every run with an
INCONCLUSIVE check — a verdict that declined to decide is information, not a contradiction. The
second states a verdict the record is itself evidence against, and it is the reading that would
let one failing verifier overrule an independent PASS with no adjudication. The conflict is
therefore recorded, the node waits, and a human resolves it
(`RunManager.resolve_verification_contradiction`, `AC4-M3-027`).

**ADR4-M3-004 (projection) — a §7.10 record requires the P7 project features, so the run manager
materialises through `ExperienceService` and never around it.** The projection is a view over a
v0.2 experience and the foreign key is `RESTRICT` in both store backends; a scheduler that wrote
a projection without materialising would produce rows PostgreSQL refuses. The cost is that a
deployment with the P7 retrieval gate closed projects nothing, and that is the right failure: a
routing memory assembled without the experience layer's redaction and moderation would be a
second, unreviewed copy of the trajectory record.

**ADR4-M2-001 (the executing-provider constraint) is superseded by the M2 seam.** M2 recorded
that a routed AGENT configuration carrying tool bindings could not be dispatched, because the
session boundary carried capability ids rather than exact bindings. The executing-provider seam
landed with that milestone's later PRs and the golden-trace test pins the flag-off path
byte-for-byte; M3's dispatch path reaches the runtime through the same seam and adds no
provider constraint of its own.
## Recorded during M8

Five decisions taken while building the activation ledger and the promotion gate. Each answers a
question the SDD asks and does not settle, so each is recorded here rather than in the SDD.

**ADR4-M8-001 (OQ-411, promotion approval) — the approver is on *every* ledger entry, rollbacks
included, and is also the entry's `created_by`.** The default was "workspace admin or research
owner, recorded on the report", and the report does record one. The entry does too, and that is
the addition: §10.3 makes activation a human act, and a withdrawal during an incident is the
activation an audit reads first. `ActivationLedger.activate` therefore takes `approved_by` for
both fields rather than attributing the row's creation to the service identity — two different
answers to "who did this" in one record is worse than either answer alone. The service identity
survives where it belongs, as `created_by` on the `RouterModelVersion` rows the deployment mints.

**ADR4-M8-002 (OQ-412 cadence, OQ-413 critical cohorts) — the gate is CSPI-MT over the
Logarithmic-Smoothing estimator, and the five cohorts are a registered list rather than a
constant.** OQ-412's default cadence is manual batch and stays that way: `PromotionEvaluator`
runs when an administrator asks, and nothing schedules it. OQ-413's five cohorts —
correctness, policy, secrets, high risk, verifier conflict — live in `promotion.v1.json` and
reach the report through `CohortResult.critical`, so "a critical regression blocks" survives a
sixth cohort being registered. Three of the five are derived from an experience record's typed
fields and `correctness` from whether its verifier reached a judgement; `secrets` has nothing in
the projection to key on and is declared through the `accretion.evaluation-cohort` label, which
is honest about the gap rather than proxying it with something that would be wrong invisibly.
The declared γ is 0.05, the non-inferiority margin is −0.02 and the primary pessimistic
estimator is LS with λ = 1/√n (R4); SNIPS and DR are computed as diagnostics and are not
permitted to decide a release, because R4's finite-sample bound does not cover them.

**ADR4-M8-003 (where the gate's constants live) — a second registered file, not
`config.v1.json`.** `RouterBenchmarkConfig` is a `StrictModel` with `extra="forbid"`, so the
promotion constants could only join the benchmark corpus by widening a frozen model, and M10c
edits `config.v1.json` for unrelated reasons. `evals/router/promotion.v1.json` keeps the two
freezes independent: the benchmark's constants move when the corpus is regenerated and the
gate's move when the gate is re-registered, and those are not the same event.

**ADR4-M8-004 (promotion events need a run context) — no event without a stored run, and no
synthesised run id.** `AgentEvent` requires a `run_id` and the event store is run-scoped end to
end, while promotion, rollback and evaluation are all reachable from an admin route with no run
in sight. Minting a run id there would attach the most consequential events in this milestone to
an execution that never happened. So `ROUTER_PROMOTION_EVALUATED`, `ROUTER_VERSION_PROMOTED` and
`ROUTER_VERSION_ROLLED_BACK` are emitted only when a caller names a run that exists, and the
durable record in every case is the ledger entry or the sealed report, which is the stronger one
anyway. A run-free promotion is fully auditable and simply has no event stream; giving §12 a
non-run-scoped channel is the real fix and is deferred to a milestone that owns the event store.

**ADR4-M8-005 (ledger contiguity is guarded in both stores, in duplicate) — deliberately not
shared.** `sequence = head + 1` and "`sequence == 1` if and only if `previous_version_id is
None`" are enforced by `_guard_activation_contiguity` in `MemoryStore` and again in
`PostgresStore`, with identical message text, and again optimistically by `ActivationLedger`
before it writes. That is three copies of one rule, and each is load-bearing: the ledger's copy
computes the values, the store's copies enforce them inside the transaction where a concurrent
promotion is visible, and PostgreSQL's `uq_router_activations_sequence` catches the race the
guard cannot. Factoring the two store copies into one helper would put the check outside the
session it has to run in. What should be shared is the *message*, and the parity test that
compares the two texts is what keeps it so.

## Recorded during M7

Three decisions taken while building the guarded bandit. Each answers a question the SDD asks and
does not settle, so each is recorded here rather than in the SDD.

**ADR4-M7-001 (OQ-402, what kind of explorer) — a conservative contextual bandit against the
deterministic router as baseline, with a conformal safety clip on top.** OQ-402's default was
"some bounded exploration"; ε-greedy is the obvious reading of that and is the wrong one, because
it spends a fixed fraction of every workspace's traffic on actions it already believes are worse
whether or not the workspace can afford it. R5 (https://www.alphaxiv.org/abs/2412.06165) gives the
guarantee an approver can actually read — cumulative explored cost stays within `(1 + α)` of what
the deterministic router would have spent **at every round** — and `CostLedger` is where that
inequality is checked, against the candidate's *upper* bound and the baseline's *lower* one. The
distribution is inverse-gap weighting, because its two properties are exactly the two an
off-policy estimator needs: every action keeps a strictly positive probability, so nothing is ever
logged at a propensity of zero, and the probability decays with the utility gap rather than on a
schedule somebody has to tune. R6's clip sits on top because inverse-gap weighting bounds *regret*
— an average over rounds — and an average is not a safety property: `β` is one minus the
split-conformal quantile of the safety losses the workspace has actually logged, exchangeable over
*projects*, and a workspace with fewer projects than the quantile's index needs gets `β = 0` and
explores nothing. That last case is the one that matters: `conformal_quantile` returns the vacuous
1.0 there by design, and reading it as "no constraint" would explore hardest exactly where there
is least evidence.

**ADR4-M7-002 (OQ-410, where the budget comes from) — α from `ObjectiveContract.exploration_policy`,
absolute caps per run and per period beside it, and all of it on the receipt.** The router does not
choose its own exploration rate: the person who approved the goal is the person entitled to say how
much of its budget may be spent learning, which is why the freeze delta put `ExplorationPolicy` on
the objective (ADR-062) rather than in a config file. A fraction alone is not a budget — a relative
bound on an expensive baseline is a large absolute number — so `max_explore_count` and `max_cost`
bind whatever α says, and the refusal names whichever one bound, because an operator told "the
inequality would break" when the cap was the binding constraint will go and change the wrong knob.
An objective sealed before the field existed cannot gain one without breaking the digest its node
contract pins, so `exploration_policy_for` also reads three `exploration.*` **labels** off a sealed
revision; all three are required together, because a missing bound is an unstated bound and not a
generous one. α, the caps, the charged cost, the credited baseline cost and the greedy action's
propensity all go on the receipt, so the inequality a decision was permitted under can be replayed
against it.

**ADR4-M7-003 (where the ledger lives) — reconstructed from EXPLORE receipts, with no table in
v0.4.** Everything the conservative inequality needs is already on the receipt that spent the
budget, and `upsert_plugin`-style immutability makes the receipt the audit record either way; a
`cost_ledger` table would be a second durable copy of a derived quantity, and the two disagreeing
is a class of bug with no external symptom. `LedgerRegistry` therefore replays a workspace's
`EXPLORE` receipts in `contract_id` order and folds in every one it has not already seen, so a
decision this process took a moment ago is charged against the next one without anybody being
told. The one thing a replay cannot rebuild is a *settlement*: `ExplorationSettlement` writes the
observed cost into the live ledger and a restart forgets it. That is deliberate and it is
conservative in the only direction a budget may be wrong in — an unsettled exploration keeps its
upper bound, so a fresh process holds a **tighter** budget than the measured one, never a looser.
A durable ledger is worth revisiting when a deployment runs long enough for the difference to
bind; it is not worth a table in v0.4.

## Parked beside v0.4

The v0.3.1 operator-UI redesign (M9 of the v0.3 ladder) is parked after its stylesheet port
completed; its remaining steps (Preflight, projection store, cosmic scene, orbit, dashboard,
release) resume from their plan when the owner reopens it.
