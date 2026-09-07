# Accretion v1.1–v1.8 forward design

**Status: planned releases; SDD document Revision 4.** Imported on 7 September
2026 from the complete document ZIP into Accretion at
`f023a67df33c2155cb282def9338c1821df84127` (v0.4.1 release documentation).
The import does not implement these releases or satisfy their acceptance gates.

## Start here

- [Full package and diagrams](package/index.html): open locally in a browser.
- [Project summary, capability review and next steps](package/accretion-v1.8-review-2026-09-06/README.md).
- [Revision 4 SDD entry point](package/Accretion_v1x_Technical_SDD_Revision_4/00_READ_ME_FIRST.md).
- [Research findings and paper plan](package/Accretion_v1x_Research_Improvements/README.md).
- [Current release baseline](../../../releases/v0.4/baseline.md) and
  [predecessor v0.4–v1.0 design](../v0.4-v1.0/00_READ_ME_FIRST.md).

GitHub displays Markdown and PNGs. Download or clone the repository to open the
interactive HTML diagrams; no application server is needed.

## Release sequence

| Target | Intended capability | Full SDD |
|---|---|---|
| v1.1 | Verified compute profiles and a bounded three-profile comparison | [v1.1](package/Accretion_v1x_Technical_SDD_Revision_4/04_Accretion_SDD_v1.1.0.md) |
| v1.2 | Compatible, promoted harness portfolios | [v1.2](package/Accretion_v1x_Technical_SDD_Revision_4/05_Accretion_SDD_v1.2.0.md) |
| v1.3 | Typed, bounded digital recovery | [v1.3](package/Accretion_v1x_Technical_SDD_Revision_4/06_Accretion_SDD_v1.3.0.md) |
| v1.4 | Provenance-backed belief, progress and experience views | [v1.4](package/Accretion_v1x_Technical_SDD_Revision_4/07_Accretion_SDD_v1.4.0.md) |
| v1.5 | Offline harness proposals with independent tests and human promotion | [v1.5](package/Accretion_v1x_Technical_SDD_Revision_4/08_Accretion_SDD_v1.5.0.md) |
| v1.6 | Optional open-weight adapter experiments | [v1.6](package/Accretion_v1x_Technical_SDD_Revision_4/09_Accretion_SDD_v1.6.0.md) |
| v1.7 | Compatible transfer priors with target simulation evidence and fallback | [v1.7](package/Accretion_v1x_Technical_SDD_Revision_4/10_Accretion_SDD_v1.7.0.md) |
| v1.8 | Advisory preparation of physical trials under the existing approval and safety gateway | [v1.8](package/Accretion_v1x_Technical_SDD_Revision_4/11_Accretion_SDD_v1.8.0.md) |

The existing router, compatibility checks, promotion process and physical
gateway retain their ownership. A later SDD does not authorize a second
dispatcher or bypass an earlier release's gate.

## User and architecture diagrams

| View | Browser | Static preview |
|---|---|---|
| User workflow | [HTML](package/accretion-v1.8-review-2026-09-06/user-journey.html) | [PNG](package/accretion-v1.8-review-2026-09-06/user-journey.png) |
| Complete logical architecture | [Interactive HTML](package/accretion-v1.8-review-2026-09-06/architecture.html) | [PNG](package/accretion-v1.8-review-2026-09-06/architecture.png) |
| Physical preparation and trial | [HTML](package/accretion-v1.8-review-2026-09-06/physical-trial.html) | [PNG](package/accretion-v1.8-review-2026-09-06/physical-trial.png) |

These views describe the planned v1.8 system. The original browser and visual
check records are dated evidence for those artifacts, not a new runtime audit.

## Research workflow and next steps

The package includes reusable [research-to-design instructions](package/Accretion_v1x_Research_Improvements/skill-snapshots/research-to-design/SKILL.md)
and [alphaXiv retrieval instructions](package/Accretion_v1x_Research_Improvements/skill-snapshots/alphaxiv-research/SKILL.md),
with reading, adaptation and paper-writing templates. They are portable reference
snapshots, not automatically installed repository agent skills. Both skills
were already installed in the originating user's Codex environment.

For the next research task, use `research-to-design` with `alphaxiv-research`:
reconcile the current implementation first, inspect primary methods and
evaluation sections, map each proposed adaptation to an existing owner, and
write a falsifiable comparison with independent outcomes and full cost.
Keep manuscript results unclaimed until measured evidence exists.

1. Reconcile each prerequisite in the predecessor design with the current
   implementation and release evidence before scheduling v1.x implementation.
2. Complete the [v1.1 readiness checklist and protocol](package/Accretion_v1x_Technical_SDD_Revision_4/24_V1_1_PILOT_READINESS_AND_PROTOCOL.md):
   inventory real traces, define three fixed profiles, qualify the verifier,
   observe effective settings, and freeze data roles, owners, numerical gates
   and budget. Its packaged readiness remains `NOT_READY_TO_EXECUTE`.
3. Create a bounded implementation plan and map its evidence to the
   [acceptance traceability](package/Accretion_v1x_Technical_SDD_Revision_4/18_ACCEPTANCE_TRACEABILITY.json).
   All 174 packaged criteria remain pending; synthetic checks do not discharge them.
4. Run the approved pilot only after its readiness and authorization gates are
   met. Retain negative and inconclusive outcomes before expanding scope.

## Validate from the repository root

After the normal developer dependency setup (`uv sync --all-groups`):

```bash
make docs-check
make future-sdd-check
```

The second command checks the complete payload manifest, then runs Revision 4's
synthetic design validator using the project's Python environment. It does not
call providers, access locked experiment data, start services or control devices.
The original package's pinned validation requirements and dated result remain
available alongside its validator for historical reproduction.

The [setup validation record](setup-validation.json) records the import checks,
link coverage, tamper-rejection tests and limits of this repository setup.

## Preservation and authority

The `package/` directory contains all 187 original files, byte for byte, from
`Accretion_Latest_Complete_Package_2026-09-07.zip` (SHA-256
`b041618ba73a28bed61dcdde50b23e1fd04856030958487aa8e49ff13cce5450`).
Its manifest digest is pinned in the repository validation script.

Revision 4 is the latest design revision in this import. Revision 3, early
research proposals and the nested `company/apps/Accretion/` document subset
are historical context. That subset is pinned to the review's
`84c3eede99c171e1f13fa37a19d15fa978e0830d` source commit; it must never be copied
over the live repository. Three historical reference documents link to files
outside that subset. The documentation checker verifies their exact identities
instead of treating those historical links as current checkout navigation.

Keep the imported directory frozen so its manifests and diagram receipts remain
valid. Put proposed amendments beside this README, following the package's
[amendment process](package/Accretion_v1x_Technical_SDD_Revision_4/13_RESEARCH_INTAKE_AND_SDD_AMENDMENT_PROCESS.md),
and produce an explicitly identified new revision when approved. Current runtime
and release claims continue to come from the live repository's release records.
