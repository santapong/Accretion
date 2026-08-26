export const meta = {
  name: 'milestone-plan',
  description: 'Plan one Accretion milestone with a spec-driven planner and an adversarial risk analyst',
  whenToUse: 'Starting a v0.3 milestone. Pass args {milestone:"M6", note:"..."}. Produces two independent perspectives to reconcile into one plan; it does not write code.',
  phases: [{ title: 'Plan', detail: 'planner and risk analyst, in parallel' }],
}

// Two perspectives, deliberately independent: the planner designs, the analyst
// attacks. Reconciling them by hand is the human gate before any code is written.

const milestone = (args && args.milestone) || 'M5'
const note = (args && args.note) || ''

const CONTEXT = `
Repository: /mnt/data/company/apps/Accretion, branch develop.
Milestone under consideration: ${milestone}.
${note ? `Operator note: ${note}\n` : ''}
Ground every claim in the repository as it is now. Read before asserting, and anchor
findings with file:line. Where the backlog or an earlier document states something,
check whether it still holds at the current HEAD rather than inheriting it.

Standing context for finishing v0.3:
- M5 research plugin (4 AC3-RES criteria), M6 frontend/admin (5 AC3-UI plus the 6
  inherited V02-UI criteria it absorbs), M7 EMA (no acceptance criteria at all), and
  M8 release hardening (the remaining inherited criteria plus two release-gate suites).
- The bare acceptance harness currently exits FAIL. M8's exit is that it exits 0.
- SDD files under docs/sdd/ are hash-manifested and must never be edited. Divergence is
  recorded as an ADR in docs/runbooks/, following ADR3-M4-001.
`

phase('Plan')
log(`Planning ${milestone}: spec-driven plan and adversarial review, in parallel`)

const results = await parallel([
  () =>
    agent(
      `${CONTEXT}\nProduce the implementation plan for ${milestone}.`,
      { label: `plan:${milestone}`, phase: 'Plan', agentType: 'sdd-milestone-planner' },
    ),
  () =>
    agent(
      `${CONTEXT}\nProduce the risk, evidence and sequencing analysis for ${milestone}. ` +
        `Assume a competent implementer who will take the shortest path that makes the gate green; ` +
        `your job is to make sure that path also happens to be correct.`,
      { label: `risk:${milestone}`, phase: 'Plan', agentType: 'milestone-risk-analyst' },
    ),
])

return {
  milestone,
  plan: results[0],
  risk: results[1],
  next: 'Reconcile these into one plan, resolve any conflict explicitly, then run milestone-build.',
}
