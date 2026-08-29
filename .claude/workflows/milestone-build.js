export const meta = {
  name: 'milestone-build',
  description: 'Build one milestone PR by PR: implement, author evidence, verify from three angles, repair until green',
  whenToUse: 'After a milestone plan is reconciled and approved. Pass args {milestone, planPath, branch?, prs:[{name, kind, scope, criteria, allowSdd?}]}. Requires a clean tree; makes one local checkpoint commit per verified PR on the milestone branch and stops at the first PR that never comes clean. It never pushes or merges.',
  phases: [
    { title: 'Build', detail: 'implement then author evidence, per PR' },
    { title: 'Verify', detail: 'spec, evidence, contracts, then the gate chain' },
    { title: 'Checkpoint', detail: 'one local commit per verified PR on the milestone branch' },
  ],
}

const milestone = (args && args.milestone) || 'M5'
const planPath = (args && args.planPath) || '/home/santapong/.claude/plans/so-let-create-a-sequential-aho.md'
const prs = (args && args.prs) || []
const maxRepairs = (args && args.maxRepairs) || 2

const REPO = '/mnt/data/company/apps/Accretion'

const branch = (args && args.branch) || `feat/v03-${milestone.toLowerCase()}`

// The SDD is normally untouchable. A PR may carry `allowSdd: "<exact change>"` when a
// governance step has authorised one specific edit; every agent on that PR is told the
// same sentence so implementers and verifiers agree on what is permitted.
function sddRule(pr) {
  if (pr && pr.allowSdd) {
    return `The ONLY permitted change under docs/sdd/ in this PR is: ${pr.allowSdd}. ` +
      `Any other change under docs/sdd/ is a blocker. Do not touch docs/sdd/future/ — it is hash-manifested.`
  }
  return 'Never edit anything under docs/sdd/ — those files are hash-manifested.'
}

function common(pr) {
  return `
Repository: ${REPO}. Milestone: ${milestone}. Working branch: ${branch}.
The approved plan is at ${planPath} — read it in full before doing anything. It is
authoritative: implement it rather than redesigning it. If you believe it is wrong,
implement it and record the concern in your report instead of silently deviating.

YOU CAN AND MUST WRITE FILES. Before anything else, prove it: create
${REPO}/.probe_${milestone}, confirm it exists, delete it. If that fails, STOP and report
that as your entire result — do not substitute research or write a plan document. A
previous run wasted itself doing read-only research when writes were blocked.

${sddRule(pr)}

Git discipline: earlier PRs of this milestone are already committed on ${branch}; the
current PR is the uncommitted working tree. NEVER run git checkout, git restore, git
stash, git reset, git clean, git commit or git push — a verifier once reverted two PRs
of uncommitted work with git checkout. A mutation check copies the file to the
scratchpad, edits, re-runs, and restores with cp, then proves byte-identity with cmp.

Local invocation traps, both of which silently ruin a run:
- pytest needs BOTH: PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 uv run --no-sync pytest -p pytest_asyncio.plugin
- Postgres is on 5433, and alembic upgrade head must run before any integration test.
`
}

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

// One local checkpoint commit per verified PR, on a milestone branch. The workflow still
// never pushes or merges; the commits give each PR a clean boundary and stop a later
// agent from destroying earlier PRs' uncommitted work.
const CHECKPOINT = {
  type: 'object',
  additionalProperties: false,
  required: ['committed', 'sha', 'summary'],
  properties: {
    committed: { type: 'boolean' },
    sha: { type: 'string' },
    summary: { type: 'string' },
  },
}

phase('Build')
log(`preparing branch ${branch}`)
const prep = await agent(
  `Repository: ${REPO}. Prepare the working branch for milestone ${milestone} and do nothing else.\n` +
    `1. Run git status --porcelain. If it is not empty, STOP: report committed=false and the dirty ` +
    `files in summary — never stash, reset or discard anything.\n` +
    `2. If branch ${branch} exists, git checkout ${branch}; otherwise git checkout -b ${branch} from ` +
    `the current HEAD (do not pull, do not switch to another base).\n` +
    `3. Report committed=true and sha = git rev-parse --short HEAD.`,
  { label: `branch:${milestone}`, phase: 'Build', agentType: 'ci-gate-runner', schema: CHECKPOINT, effort: 'low' },
)
if (!prep || !prep.committed) {
  log(`branch preparation refused: ${prep ? prep.summary : 'no result'} — nothing built`)
  return { milestone, branch, prs: [], next: `Clean the working tree, then re-run. ${prep ? prep.summary : ''}` }
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
    `${common(pr)}\nYou are the IMPLEMENTER for "${pr.name}".\n\nSCOPE:\n${pr.scope}\n\n${claims}\n\n` +
      `Write complete, production-quality code — no TODOs, no stubs, no NotImplementedError. ` +
      `Run the gates yourself and fix what you break. Never weaken a test to make a gate pass. ` +
      `Do not commit; leave the changes in the working tree. Report files touched and gates run green.`,
    { label: `implement:${label}`, phase: 'Build', agentType: implementer },
  )

  log(`${label}: authoring evidence`)
  await agent(
    `${common(pr)}\nYou are the EVIDENCE AUTHOR for "${pr.name}". The feature is already in the ` +
      `working tree; you did not write it, and that is the point.\n\nSCOPE:\n${pr.scope}\n\n${claims}\n\n` +
      `Write the claiming tests and any fakes, update docs/acceptance/criteria.toml, and ` +
      `mutation-check every claiming test you add: neuter the thing under test, confirm the test ` +
      `fails, restore, and verify byte-identity. Report each mutation and its result.`,
    { label: `evidence:${label}`, phase: 'Build', agentType: 'evidence-author' },
  )

  let round = 0
  let verdicts = []
  const history = []
  let clean = false
  while (round <= maxRepairs) {
    phase('Verify')
    log(`${label}: verification round ${round}`)

    const reviews = await parallel([
      () =>
        agent(
          `${common(pr)}\nVerify "${pr.name}" against the SDD. ${claims}`,
          { label: `spec:${label}:${round}`, phase: 'Verify', agentType: 'sdd-spec-verifier', schema: VERDICT },
        ),
      () =>
        agent(
          `${common(pr)}\nAudit the acceptance evidence for "${pr.name}". ${claims}\n` +
            `Run the mutations yourself; do not take the evidence author's word for them.`,
          { label: `evidence-audit:${label}:${round}`, phase: 'Verify', agentType: 'acceptance-auditor', schema: VERDICT },
        ),
      () =>
        agent(
          `${common(pr)}\nReview the contract, model and migration changes in "${pr.name}".`,
          { label: `contracts:${label}:${round}`, phase: 'Verify', agentType: 'contract-guardian', schema: VERDICT },
        ),
    ])

    const gates = await agent(
      `${common(pr)}\nRun the full gate chain for "${pr.name}" and report faithfully. ${claims}\n` +
        `FAIL FAST: if git status is clean and there is no diff against develop, nothing was built — ` +
        `report that immediately and do not run the gates, since they would be false greens.`,
      { label: `gates:${label}:${round}`, phase: 'Verify', agentType: 'ci-gate-runner', schema: VERDICT },
    )

    verdicts = [...reviews, gates]
    history.push({ round, verdicts: verdicts.map((v) => (v ? { passed: v.passed, summary: v.summary, findings: v.findings } : null)) })
    const open = blocking(verdicts)
    if (open.length === 0) {
      log(`${label}: verification clean after ${round} repair round(s)`)
      clean = true
      break
    }
    if (round === maxRepairs) {
      log(`${label}: STILL FAILING after ${maxRepairs} repair rounds — ${open.length} open finding(s)`)
      break
    }

    round += 1
    log(`${label}: ${open.length} blocking finding(s), repairing`)
    await agent(
      `${common(pr)}\nYou are the REPAIR agent for "${pr.name}".\n\nFINDINGS TO FIX:\n` +
        JSON.stringify(open, null, 2) +
        `\n\nFix the root cause, not the symptom. Never weaken, delete, skip or xfail a test to ` +
        `make a gate pass — if a test is genuinely wrong, fix it so it still proves the same ` +
        `criterion, and say so. Re-run the relevant gates before reporting.`,
      { label: `repair:${label}:${round}`, phase: 'Verify', agentType: implementer },
    )
  }

  pr.verdicts = verdicts.map((v) => (v ? { passed: v.passed, summary: v.summary, findings: v.findings } : null))
  pr.history = history

  if (!clean) {
    log(`${label}: not checkpointed — stopping the milestone here, tree left for a human`)
    pr.checkpoint = { committed: false, sha: '', summary: 'verification never came clean' }
    break
  }

  phase('Checkpoint')
  log(`${label}: checkpoint commit on ${branch}`)
  pr.checkpoint = await agent(
    `Repository: ${REPO}. Make the checkpoint commit for "${pr.name}" on branch ${branch} and do nothing else.\n` +
      `1. git rev-parse --abbrev-ref HEAD must print ${branch}; if not, STOP and report committed=false.\n` +
      `2. Remove ${REPO}/.probe_${milestone} if it exists. git add -A. Refuse (committed=false) if the ` +
      `staged set contains .env, node_modules, .venv, or anything under docs/sdd/future/.\n` +
      `3. git commit -m "${pr.name} [${milestone} checkpoint]" with the body "Claims: ${(pr.criteria || []).join(', ') || 'none'}." ` +
      `and the trailers "Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>" and ` +
      `"Claude-Session: https://claude.ai/code/session_012tskboxpxk8MYS4f6k5Se4". Do NOT push.\n` +
      `4. Report committed=true, sha = git rev-parse --short HEAD, and the --stat line in summary.`,
    { label: `checkpoint:${label}`, phase: 'Checkpoint', agentType: 'ci-gate-runner', schema: CHECKPOINT, effort: 'low' },
  )
}

return {
  milestone,
  branch,
  prs: prs.map((p) => ({
    name: p.name,
    criteria: p.criteria || [],
    verdicts: p.verdicts,
    history: p.history,
    checkpoint: p.checkpoint,
  })),
  next: `Review the checkpoint commits on ${branch}, then split them into PRs and merge. Nothing has been pushed.`,
}
