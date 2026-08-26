export const meta = {
  name: 'milestone-build',
  description: 'Build one milestone PR by PR: implement, author evidence, verify from three angles, repair until green',
  whenToUse: 'After a milestone plan is reconciled and approved. Pass args {milestone, planPath, prs:[{name, kind, scope, criteria}]}. Leaves changes in the working tree for review; it does not commit, push, or merge.',
  phases: [
    { title: 'Build', detail: 'implement then author evidence, per PR' },
    { title: 'Verify', detail: 'spec, evidence, contracts, then the gate chain' },
  ],
}

const milestone = (args && args.milestone) || 'M5'
const planPath = (args && args.planPath) || '/home/santapong/.claude/plans/so-let-create-a-sequential-aho.md'
const prs = (args && args.prs) || []
const maxRepairs = (args && args.maxRepairs) || 2

const REPO = '/mnt/data/company/apps/Accretion'

const COMMON = `
Repository: ${REPO}. Milestone: ${milestone}.
The approved plan is at ${planPath} — read it in full before doing anything. It is
authoritative: implement it rather than redesigning it. If you believe it is wrong,
implement it and record the concern in your report instead of silently deviating.

YOU CAN AND MUST WRITE FILES. Before anything else, prove it: create
${REPO}/.probe_${milestone}, confirm it exists, delete it. If that fails, STOP and report
that as your entire result — do not substitute research or write a plan document. A
previous run wasted itself doing read-only research when writes were blocked.

Never edit anything under docs/sdd/ — those files are hash-manifested.

Local invocation traps, both of which silently ruin a run:
- pytest needs BOTH: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
- Postgres is on 5433, and alembic upgrade head must run before any integration test.
`

const VERDICT = {
  type: 'object',
  additionalProperties: false,
  required: ['passed', 'summary', 'findings'],
  properties: {
    passed: { type: 'boolean', description: 'true only if nothing blocking remains' },
    summary: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['severity', 'file', 'problem', 'required_fix'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          file: { type: 'string' },
          problem: { type: 'string' },
          required_fix: { type: 'string' },
        },
      },
    },
  },
}

function blocking(verdicts) {
  const open = []
  for (const v of verdicts) {
    if (!v) continue
    for (const f of v.findings || []) {
      if (f.severity === 'blocker' || f.severity === 'major') open.push(f)
    }
  }
  return open
}

for (const pr of prs) {
  const label = `${milestone}:${pr.name}`
  const implementer = pr.kind === 'frontend' ? 'frontend-implementer' : 'milestone-implementer'
  const claims = (pr.criteria && pr.criteria.length)
    ? `This PR claims: ${pr.criteria.join(', ')}.`
    : 'This PR claims no acceptance criteria — a failure here must be unambiguous.'

  phase('Build')
  log(`${label}: implementing with ${implementer}`)

  await agent(
    `${COMMON}\nYou are the IMPLEMENTER for "${pr.name}".\n\nSCOPE:\n${pr.scope}\n\n${claims}\n\n` +
      `Write complete, production-quality code — no TODOs, no stubs, no NotImplementedError. ` +
      `Run the gates yourself and fix what you break. Never weaken a test to make a gate pass. ` +
      `Do not commit; leave the changes in the working tree. Report files touched and gates run green.`,
    { label: `implement:${label}`, phase: 'Build', agentType: implementer },
  )

  log(`${label}: authoring evidence`)
  await agent(
    `${COMMON}\nYou are the EVIDENCE AUTHOR for "${pr.name}". The feature is already in the ` +
      `working tree; you did not write it, and that is the point.\n\nSCOPE:\n${pr.scope}\n\n${claims}\n\n` +
      `Write the claiming tests and any fakes, update docs/acceptance/criteria.toml, and ` +
      `mutation-check every claiming test you add: neuter the thing under test, confirm the test ` +
      `fails, restore, and verify byte-identity. Report each mutation and its result.`,
    { label: `evidence:${label}`, phase: 'Build', agentType: 'evidence-author' },
  )

  let round = 0
  let verdicts = []
  while (round <= maxRepairs) {
    phase('Verify')
    log(`${label}: verification round ${round}`)

    const reviews = await parallel([
      () =>
        agent(
          `${COMMON}\nVerify "${pr.name}" against the SDD. ${claims}`,
          { label: `spec:${label}:${round}`, phase: 'Verify', agentType: 'sdd-spec-verifier', schema: VERDICT },
        ),
      () =>
        agent(
          `${COMMON}\nAudit the acceptance evidence for "${pr.name}". ${claims}\n` +
            `Run the mutations yourself; do not take the evidence author's word for them.`,
          { label: `evidence-audit:${label}:${round}`, phase: 'Verify', agentType: 'acceptance-auditor', schema: VERDICT },
        ),
      () =>
        agent(
          `${COMMON}\nReview the contract, model and migration changes in "${pr.name}".`,
          { label: `contracts:${label}:${round}`, phase: 'Verify', agentType: 'contract-guardian', schema: VERDICT },
        ),
    ])

    const gates = await agent(
      `${COMMON}\nRun the full gate chain for "${pr.name}" and report faithfully. ${claims}\n` +
        `FAIL FAST: if git status is clean and there is no diff against develop, nothing was built — ` +
        `report that immediately and do not run the gates, since they would be false greens.`,
      { label: `gates:${label}:${round}`, phase: 'Verify', agentType: 'ci-gate-runner', schema: VERDICT },
    )

    verdicts = [...reviews, gates]
    const open = blocking(verdicts)
    if (open.length === 0) {
      log(`${label}: verification clean after ${round} repair round(s)`)
      break
    }
    if (round === maxRepairs) {
      log(`${label}: STILL FAILING after ${maxRepairs} repair rounds — ${open.length} open finding(s)`)
      break
    }

    round += 1
    log(`${label}: ${open.length} blocking finding(s), repairing`)
    await agent(
      `${COMMON}\nYou are the REPAIR agent for "${pr.name}".\n\nFINDINGS TO FIX:\n` +
        JSON.stringify(open, null, 2) +
        `\n\nFix the root cause, not the symptom. Never weaken, delete, skip or xfail a test to ` +
        `make a gate pass — if a test is genuinely wrong, fix it so it still proves the same ` +
        `criterion, and say so. Re-run the relevant gates before reporting.`,
      { label: `repair:${label}:${round}`, phase: 'Verify', agentType: implementer },
    )
  }

  pr.verdicts = verdicts.map((v) => (v ? { passed: v.passed, summary: v.summary, findings: v.findings } : null))
}

return {
  milestone,
  prs: prs.map((p) => ({ name: p.name, criteria: p.criteria || [], verdicts: p.verdicts })),
  next: 'Review the diff, then open and merge the PRs. Nothing has been committed.',
}
