# v0.4 release closure audit — 2026-09-08

Accretion v0.4.0 and v0.4.1 are already released in the inspected repository history.
The smallest release-closure job is to reconcile the active documentation with that
history and make the remaining hardening and research questions explicit. The
milestones must not be restarted from the stale backlog table.

This is a planning audit, not a new release authorization or an execution result.
The runtime and research audits in the same planning effort own the deeper review
of implementation gaps and future experiments.

## Scope and verification level

- Inspected base: `8ded8bab151267ec8e06182f52543dabed6eaca7`, the #168 integration of
  the frozen v1.1–v1.8 package, on 2026-09-08 (Asia/Bangkok).
- Isolated branch: `docs/v04-release-audit`. Only this report is changed.
- **Current read-only verification:** local Git objects, tree identities, file
  contents, AST inspection of literal acceptance markers, acceptance-policy
  validation and frontend pointer validation, research-document SHA-256 values,
  and the access log's line count. No locked corpus was read or reevaluated.
- **Historical reported verification:** the test, browser, migration, release-gate
  and CI results recorded in the released audits. They were not rerun here.
- No provider calls, training, paid pilot, service changes, runtime changes, remote
  publication, branch deletion or full application suite occurred in this audit.
- Remote-tracking refs are locally available evidence. No fresh remote request was
  made by this audit; present GitHub checks or branch protection were not inspected.

All line references below describe the inspected base, before this report is
added. Relative links resolve in an ordinary checkout; `#L` suffixes also identify
the source line when viewed on GitHub.

## Release identity reconciled

The [baseline](../baseline.md#L10) records the following identities. `git show` and
`git for-each-ref` currently reproduce them from the local objects:

| Release | Annotated tag object | Peeled release commit | Tree |
|---|---|---|---|
| v0.4.0 | `c4067684a7de6f820cdf0703c6d70afc5a29ebae` | `dd1d9f300ba05dc5875b964322c34b47a5288c39` | `2e59ad70ad36b0934c189ec698bbf7658928b57d` |
| v0.4.1 | `3674ab717f0fd10a71f71140be29137955a491eb` | `f60c7faae68a4ebae7ad4e15696746957690ce7b` | `2e61bcc7b34de7ac56be9fdd66b9eececd19cddd` |

The authorized integration commits `08a695bcc642fb11bf6b67cbd524ae727610849c`
(v0.4.0) and `59b8efdafd0adcedf2955eacfe9b2b9fb4445da3` (v0.4.1) have the same
trees as their corresponding release commits. The different commits are expected
under the protected squash-promotion procedure in
[branch policy, lines 41–64](../../../governance/branch-policy.md#L41).

At this base, `src/`, `migrations/` and `apps/ui/` have no changes relative to the
v0.4.1 tag. The subsequent #168 addition changes documentation validation tooling,
its tests and configuration, and imports future designs. It does not implement
v1.1–v1.8 or reopen the completed v0.4 release program.

## Milestone and acceptance matrix

The normative exits are [SDD §19, lines 1133–1149](../../../sdd/Accretion_SDD_v0.4.md#L1133).
The normative fifty MUST rows are [§20, lines 1153–1254](../../../sdd/Accretion_SDD_v0.4.md#L1153).
M0 owns no behavioral acceptance row. The closure PRs and historical passing
acceptance evidence are recorded in
[acceptance-baseline.md, lines 35–53](../acceptance-baseline.md#L35).

**Status convention:** “delivered” below means the named closure is in inspected
Git history and the release audit reports passing evidence. The current inspection
found the listed executable witness; it did not rerun the witness.

| Milestone | Rows | Reconciled status | Closure evidence | Representative current executable witness |
|---|---:|---|---|---|
| M0 contracts/freeze | 0 | Delivered, amended by the recorded freeze delta | #123; [freeze record lines 3–19](../m0-freeze.md#L3), [21-schema delta lines 271–300](../m0-freeze.md#L271) | [persistence-model tests](../../../../tests/test_v04_m0_persistence_models.py) and [schema exporter](../../../../scripts/export_contract_schemas.py) |
| M1 compatibility | 4 | Delivered | #128 and #136 | [policy gates line 444](../../../../tests/test_v04_m1_gates.py#L444); [joint compatibility line 579](../../../../tests/test_v04_m1_compatibility.py#L579) |
| M2 deterministic selector | 11 | Delivered; backlog's review-pending state is obsolete | #139, `9311d5a` | [freeze line 217](../../../../tests/test_v04_m2_freeze.py#L217); [receipt-before-dispatch line 192](../../../../tests/test_v04_m2_end_to_end.py#L192) |
| M3 feedback/recovery | 13 | Delivered | #146, `f5c2f1d` | [conflict end to end line 348](../../../../tests/test_v04_m3_e2e.py#L348); [visibility line 559](../../../../tests/test_v04_m3_experience.py#L559) |
| M4 ranker/calibration | 1 | Delivered; existence does not authorize a live learned policy | #138, `42067cd` | [holdout gate line 458](../../../../tests/test_v04_m4_train.py#L458) |
| M5 cold start/adapter | 1 | Delivered | #141, `3e596d9` | [cross-domain bound line 498](../../../../tests/test_v04_m5_coldstart.py#L498) |
| M6 shadow routing | 2 | Delivered under the disclosed interim evidence floor | #145, `5c1ed71` | [execution unchanged line 359](../../../../tests/test_v04_m6_e2e.py#L359); [comparison report line 206](../../../../tests/test_v04_m6_report_api.py#L206) |
| M7 guarded bandit | 3 | Delivered; production rollout/cap questions belong to the runtime audit | #151, `84c3eed` | [low-risk gate line 628](../../../../tests/test_v04_m7_bandit.py#L628); [propensity line 759](../../../../tests/test_v04_m7_bandit.py#L759) |
| M8 promotion/rollback | 6 | Delivered | #147, `71893d4` | [holdout evaluation line 745](../../../../tests/test_v04_m8_evaluator.py#L745); [rollback line 576](../../../../tests/test_v04_m8_promotion.py#L576) |
| M9 Experiment Studio | 3 | Delivered: two frontend policy rows, one Python row | #153, `df4e389` | [frontend policy lines 54–55](../../../acceptance/criteria.toml#L54); [correlation line 190](../../../../tests/test_v04_m9_correlation.py#L190) |
| M10 research instrument | 6 | Delivered with scoped, disclosed replay findings | #156, `cea73eb` | [split guard line 189](../../../../tests/test_v04_m10_locked_test.py#L189); [claim guard line 398](../../../../tests/test_v04_m10_locked_test.py#L398); [ablations line 430](../../../../tests/test_v04_m10_ablations.py#L430) |

The current static inspection found **167 total criteria, 50 v0.4 criteria,
48 v0.4 Python-test policies and 2 v0.4 frontend policies**. All 48 Python-policy
rows have at least one literal acceptance marker in the test sources. No v0.4
row is `not_yet_due`; acceptance-policy validation returned no errors and its
frontend evidence pointers resolve. This is coverage/pointer verification, not a
new `make acceptance` pass.

The historical release line is `in scope: 167   proven: 159   unmet MUST: 0`:
159 Python-proven rows + 5 frontend rows + 3 manual rows = 167. It does not mean
159/167 acceptance. The three manual records expire on 2027-02-28; they are
prior signed-in provider evidence, not a priced v0.4 routing pilot.
[Baseline lines 46–69](../baseline.md#L46) and
[verification policy lines 57–70](../../../acceptance/criteria.toml#L57).

## What v0.4.1 actually completed

The patch is recorded in [notes lines 236–257](../notes.md#L236) and
[audit lines 223–239](../audit.md#L223):

| Change | Delivered evidence | Remaining implication |
|---|---|---|
| Run-projected experience joins into snapshots | #161; [ADR4.1-001 lines 647–675](../backlog.md#L647) | The M9-era missing-join narrative is historical, not a current task. |
| Selector defaults use the registered utility weights | #162; [ADR4.1-002 lines 676–699](../backlog.md#L676) | Existing immutable objectives retain their weights; do not rewrite them. |
| Explicit pooling rule and amendment 1 | #163 and #164; [ADR4.1-003/004 lines 700–744](../backlog.md#L700) | Amendment 1 is frozen and the authorized re-read has happened. New tuning or a fresh read requires a distinct prospective research decision. |
| CI release-branch style-diff handling | #160; [notes line 257](../notes.md#L257) | A documented skip is shipped, not a missing release step. |
| Release metadata, protected promotion, tag, baseline identities | #165–#167; [baseline lines 71–84](../baseline.md#L71) | Do not recreate or move either release tag. |

Current SHA-256 checks reproduced the frozen pre-registration digest
`6b45998b3a9847298ba878a6414211ce38d64c775e5addd587509fdd70cf04ea` and
amendment-1 digest
`139f60692de5e9faa545d3e169fcf92e8aab3e45ac41a7ec1b0891cdae4e3914`.
The [access log](../../../research/v0.4/access-log.jsonl) has four rows. Counting
rows and hashing those two protocol documents did not access locked outcomes.

The v0.4.1 audit historically reports 3,411 Python passes, 6 skips and 2
deselections, a 47-test PostgreSQL rerun, 286 UI passes, acceptance with zero
unmet MUSTs, and all five release gates passing. Preserve the date/base and
these qualifications when quoting those counts.

## Exact documentation correction inventory

These are proposed changes in live documentation, not edits to the preserved
v1.1–v1.8 package or to historical tag objects.

| Priority | Current source and contradiction | Smallest correction and verification |
|---|---|---|
| P1 | [Release index lines 3–12 and 32–33](../README.md#L3) says acceptance complete but awaiting the release PR and links drafts. | State v0.4.1 released, cite both baseline identities and the dated audit. Retain milestone execution evidence as historical. |
| P1 | [Backlog lines 3–20](../backlog.md#L3) says M2 pending and M6–M9 not started. | Replace only the active summary/table with the reconciled matrix and introduce a separate remaining-work table. Link closure PRs and plans; do not erase recorded ADR history. |
| P1 | [Project README lines 15, 30, 73–74, 329 and 370](../../../../README.md#L73) presents v0.4.0 as current and `develop` as work toward v0.4. | Update current-release labels and quick-start tag to v0.4.1; describe `develop` from the current baseline. Preserve descriptions explicitly discussing the v0.4.0 release. |
| P1 | [Documentation hub lines 37–40](../../../README.md#L37) sends readers to building v0.4 and draft release preparation. | Distinguish released milestone evidence, active closure/hardening plan and future design packages. Add this planning effort's canonical entry point as required by documentation policy. |
| P1 | [Audit lines 3–12](../audit.md#L3) simultaneously says no decision may be read from the draft and records an authorized candidate. | Label the original dated measurement as a completed historical audit, add the actual release/baseline pointer, and retain the v0.4.1 addendum. Do not manufacture fresh measurements. |
| P1 | [Research index lines 18 and 24–37](../../../research/README.md#L18) says two access-log rows while also describing amendment 1's completed re-read. | Label two as the original v0.4.0 read and four as the total through amendment 1. Keep original and amendment results separately attributed. Hashes and row count must stay unchanged. |
| P1 | [Notes lines 159–163](../notes.md#L159) says restart forgets exploration spend, conflicting with the shipped receipt replay. | Describe the in-process cache and deliberate loss of settlement adjustments precisely. [LedgerRegistry lines 322–372](../../../../src/accretion/routing/bandit.py#L322) reconstructs charges; [Postgres parity test line 247](../../../../tests/test_v04_m7_postgres_store.py#L247) names the witness. Do not infer cross-process or malformed-receipt guarantees from replay alone; the runtime audit owns those. |
| P2 | [Baseline lines 25–28](../baseline.md#L25) proposes `git diff origin/develop origin/main` to prove the old promotion after both branches can move. | Use the pinned authorized and release commit pairs already recorded in the baseline. Preserve all frozen identities. |
| P2 | [Acceptance policy lines 157–160](../../../acceptance/criteria.toml#L157) and [release index lines 7–8](../README.md#L7) say all fifty rows have a claiming test and need no policy row. | Say 48 Python claims and two frontend policies; only the bottom milestone-deferral section is empty. No policy semantics, marker, criterion or threshold changes. |
| P2 | [SDD OQ-401 line 1293](../../../sdd/Accretion_SDD_v0.4.md#L1293) names a dependency selection while [ADR4-M4-001 lines 142 onward](../backlog.md#L142) records the dependency-free in-repo learner. | Add a release-level decision map pointing to the actual resolved implementation. A normative SDD correction, if desired, must use an explicit amendment; do not silently edit the frozen source. |
| P2 | [SDD OQ-417/418/420 lines 1309–1312](../../../sdd/Accretion_SDD_v0.4.md#L1309) has blank decisions despite partial/resolved decisions elsewhere. | In that decision map record OQ-417 as implemented syntax with closed taxonomy deferred ([M2 lines 65–66](../m2-plan.md#L65)); OQ-418 structural independence implemented ([verification lines 207–243](../../../../src/accretion/feedback/verification.py#L207)); OQ-420 no reserved slot, ADR-059 ([M0 lines 28–31](../m0-plan.md#L28)). Do not call the still-deferred taxonomy complete. |
| P2 | [Backlog lines 31–58](../backlog.md#L31) schedules upcasting/digest convergence for M8, then records three converged and four legacy sites at lines 62–87. | Link the actual M8 upcaster and final digest outcome from the active backlog. Preserve the four persisted digest sites; their migration is not a safe mechanical cleanup. [Upcast module](../../../../src/accretion/contracts/upcast.py) is present. |
| P2 | [M2 lines 3–18](../m2-plan.md#L3), [M9 lines 72 onward](../m9-plan.md#L72), and [M10 counts lines 130 onward](../m10-plan.md#L130) can be mistaken for current blockers. | Add concise historical scope/closure pointers where necessary. Do not rewrite old test counts, the old failing-join observation, or the fact that the original worktree lacked PostgreSQL. |

## Bounded release-closure work packages

| Package | Owner and scope | Dependencies | Acceptance and validation |
|---|---|---|---|
| RC-1: Reconcile release navigation | One documentation worker owns the root README, docs hub, v0.4 index, active backlog header/table and audit status. | This audit's tag/acceptance matrix; no runtime work. | Every active entry point identifies v0.4.1 and completed M0–M10. Current status, historical results and planned capabilities are labeled. `make docs-check`, local-link checks and `git diff --check` pass. |
| RC-2: Reconcile limitations and decision status | One documentation worker owns the notes' ledger explanation, the release-level open-question/deferral map, research-index chronology and historical milestone banners. | Final runtime/research audit findings; RC-1 vocabulary. | Every unresolved item is distinguished from a delivered mechanism, has an owner/next decision, and references a real source. Protocol/corpus/access-log hashes are unchanged; no new result is asserted. |
| RC-3: Verify integrated closure | Coordinator owns pinned identity recheck, integrated diff review, documentation checks and evidence ledger. | RC-1/RC-2 merged locally, any separately approved hardening changes finished. | Report exact candidate SHA and files changed. For docs-only closure, do not rerun the full suite or paid tests. For actual runtime changes, use the runtime plan's targeted tests and the normal candidate CI/release gates. Do not inherit historical counts as fresh passes. |

Parallel work should have disjoint file ownership. RC-1 and RC-2 can run in
separate worktrees after their required audit inputs are ready; the coordinator
resolves shared terminology and updates the central plan. Runtime hardening and
prospective research are separate work packages in the master plan, not extra
acceptance rows invented inside this report.

## Explicit deferrals and unknowns

- **The priced real-provider routing pilot remains not run.** Prior CLI manual
  acceptance is not that pilot. A costed, approved pilot and binding-readiness
  evidence belong to the separate runtime/research plan; no call is authorized by
  writing this audit. [Notes lines 122–127](../notes.md#L122).
- **OQ-409 remains genuinely open:** the default is 30 complete pairs at workspace
  scope, not a completed power calculation or an implemented per-configuration
  quota. The SDD's nine-pairs-per-configuration wording and the actual interim
  mechanism need careful reconciliation before an evidence-based rollout.
  [Backlog lines 395–410](../backlog.md#L395),
  [shadow.py lines 115–120](../../../../src/accretion/routing/shadow.py#L115).
- **OQ-419 public benchmark name is intentionally deferred** to protocol
  publication preparation. Naming is not a reason to rerun M10.
  [Notes lines 150–151](../notes.md#L150).
- **The four v0.3 carryovers are still explicitly outside v0.4 acceptance:**
  workspace-shared/`SERVICE_ACCOUNT` authorization, session enumeration, real IdP
  interoperability evidence and token-exchange egress allowlisting.
  [Backlog lines 24–31](../backlog.md#L24). Do not silently add enterprise work to
  this closure.
- **Exact routed AGENT tool-binding support and uncertain dispatch recovery** are
  disclosed limitations, not evidence of exactly-once execution or unrestricted
  live-provider readiness. The runtime audit decides the bounded next patch.
  [Notes lines 164–168](../notes.md#L164).
- **Four legacy digest sites remain byte-frozen.** A rehash requires compatibility
  and migration evidence, not a broad substitution of hashing helpers.
  [Backlog lines 62–87](../backlog.md#L62).
- **The v0.3.1 operator UI ladder and v0.5+/v1.x capabilities remain parked/planned.**
  SDD v0.4 explicitly forbids importing v0.5 robotics or v0.8 learned-graph work
  into its backlog. [SDD lines 1316–1326](../../../sdd/Accretion_SDD_v0.4.md#L1316).
- **No new scientific superiority claim follows from release acceptance.** The
  engineering gates passed historically while benchmark limitations remained.
  The research audit owns prospective protocol changes and interpretation; this
  report preserves the frozen evidence and identifies stale summaries only.
- Current remote CI, operational deployment health, actual active-router state and
  production spend were not inspected. None is inferred from local source or tags.

## Reproduce the non-executing identity checks

Run from the repository root. These commands inspect committed identities and
protocol files without calling a provider or reading the locked outcome corpus:

```bash
git show -s --format='%H %T %s' v0.4.0^{} v0.4.1^{}
git diff --exit-code 08a695bcc642fb11bf6b67cbd524ae727610849c dd1d9f300ba05dc5875b964322c34b47a5288c39
git diff --exit-code 59b8efdafd0adcedf2955eacfe9b2b9fb4445da3 f60c7faae68a4ebae7ad4e15696746957690ce7b
sha256sum docs/research/v0.4/preregistration.md docs/research/v0.4/amendment-1.md
wc -l docs/research/v0.4/access-log.jsonl
make docs-check
git diff --check
```

`check_acceptance.py --no-tests` is not a substitute for `make acceptance`: it does
not discover test claims and can classify unexecuted criteria as uncovered. The
static source audit above deliberately reports marker/pointer coverage instead
of presenting that mode as a successful acceptance run.

## Validation of this report

On 2026-09-08, `python scripts/check_docs.py` passed over the staged report and
existing tracked documentation: 222 Markdown files, 18 SVGs, and all 187 preserved
future-package files unchanged. `git diff --cached --check` passed. These are
current documentation/integrity checks only. The coordinator must link this
report through the master plan and documentation hub during integration.
