# Research intelligence runbook

How to operate the research plugin introduced by v0.3 M5. The normative contract is
[Accretion SDD v0.3](../sdd/Accretion_SDD_v0.3.md) §9.1, §10, §22, and §27.

The governing rule is inherited from M4 (ADR3-006): **manifests are requests, policy is
authority**. Nothing below changes that. What M5 adds is a second rule of the same
shape, one level down: **connector output is a request, the normalizer is authority**.
A research backend can say anything it likes about a paper. It cannot say anything at
all about how much that claim should be trusted.

## What ships

One bundled plugin, `accretion-research`, declaring five skills, five capabilities and
two MCP servers, plus three verifiers registered outside the manifest.

| Canonical capability id | What it does |
|---|---|
| `research.literature.search` | Literature search over the bound backend. |
| `research.paper.fetch` | Retrieve one paper's record. |
| `research.metadata.resolve` | Resolve metadata for a claimed identifier. |
| `research.citation.verify` | Verify a claimed citation against a resolver. |
| `github.search` | Implementation survey over public repositories. |

| Verifier id | Reads |
|---|---|
| `research-provenance` | That the stored provenance names connector, capability, query, timestamp and source. |
| `research-citation` | The resolver's answer for a claimed identifier, recomputed. |
| `research-evidence-quality` | The record as stored, for the quality signal. |

The capability ids above are **canonical**: they name what a workflow needs and never
which connector serves it. That is the whole of AC3-RES-02 and of the §27 exit
criterion, and it is why a workflow template contains no connector, endpoint, tool
name, or wire shape anywhere in its bytes.

## The two backends

Two connectors, `research-openalex` and `research-crossref`, are bound to the *same*
five capability ids. They are deliberately given different upstream wire shapes so
normalization has real work to do rather than a rename to perform.

```text
workflow node ──> capability id ──> resolver ──> binding ──> connector ──> MCP server
                  (canonical)                    (enabled)   (swappable)   (per backend)
                                                     │
                                            output_transform_ref
                                                     ↓
                                            EvidenceCandidate[]
```

### Swapping the backend

The swap is a change to two rows and nothing else:

```sql
UPDATE capability_bindings SET enabled = false WHERE connector_id = 'research-openalex';
UPDATE capability_bindings SET enabled = true  WHERE connector_id = 'research-crossref';
```

No workflow is edited, no capability id moves, no schema changes, and no code is
touched. `tests/test_v03_m5_research.py` asserts that the set of binding fields that
differ across the swap is exactly `{"enabled"}` — so a swap that quietly changed
anything else would fail rather than pass silently.

`ACCRETION_RESEARCH_CONNECTOR_ID` names the connector the workflow binds to at seed
time. Changing it changes which backend is enabled; it cannot change a capability id.

## The trust model

`EvidenceTrust` is ordered low to high:

```text
QUARANTINED  <  UNVERIFIED  <  CORROBORATED  <  VERIFIED
```

| Label | Assigned when |
|---|---|
| `VERIFIED` | Both the provenance and the citation verifier returned `PASS`. |
| `CORROBORATED` | Provenance verified; the citation could not be confirmed either way. |
| `UNVERIFIED` | No verifier has looked at the record yet. This is the state every record is written in. |
| `QUARANTINED` | Any verifier returned `FAIL`. |

### Unverified evidence is unrankable, not low-scored

`EvidenceRecord.trust_score` is `None` for `UNVERIFIED` and `QUARANTINED` records, and
a model validator refuses to construct such a record with a score. That is a deliberate
choice and it is the substance of AC3-RES-04.

The alternative — give unverified text a low score and let it compete — fails in the
one case that matters. A connector controls the *content* of what it returns, so it
controls every content-derived signal: recency, snippet overlap, title similarity, the
number of authors. A low-but-nonzero score is therefore something an upstream can climb
by writing better text, which is exactly the capability an adversarial or merely sloppy
source should not have. Scoring is a knob the source can turn; rankability is not.

Making the score structurally absent means unverified evidence cannot appear in a
ranking at all, however good it looks. The acceptance test drives the extreme case
directly: an unverified record with `similarity = 1.0` and a verified record with
`similarity = 0.01`, and the verified record must still sort first. It does, because the
unverified one has no score to sort by.

This mirrors `CandidateScore.total_score`, which is already `None` unless verifiers
accepted the candidate. M5 did not invent the pattern; it applied the existing one to a
new kind of evidence.

### Trust is never read from connector output

`EvidenceCandidate` has **no trust field**. A payload that says `"trust": "VERIFIED"`
has nowhere to land: `StrictModel` is `extra="forbid"`, so the field is rejected at
validation, and even if it were accepted the label is assigned by
`CapabilityGateway._record_evidence` as a literal. The poisoning test asserts this by
sending exactly that payload and reading the record back as `UNVERIFIED`.

## Evidence provenance

`EvidenceProvenance` makes AC3-RES-03 a property of the type rather than of reviewer
discipline. Connector, capability, query, timestamp and source identifier are all
required and non-optional, so evidence that cannot say where it came from cannot be
constructed at all. `binding_id` and `connection_id` are optional because a
deterministic local source has neither.

Records are content-addressed: a candidate whose `content_digest` is already stored for
a run is not written twice, so one paper reached through both backends collapses to a
single record carrying the provenance of whichever backend served it first.

Read a run's evidence with:

```
GET /api/v1/runs/{run_id}/research-evidence?workspace_id={workspace_id}
```

The route is read-only by construction. Evidence is written on the gateway's execution
path, where the trust label is assigned, and there is no HTTP verb anywhere that can
insert a record or relabel one. Ordering is `(created_at, evidence_id)` in all three
store implementations, so two responses can be diffed without re-sorting.

`workspace_id` is a required parameter rather than something derived from the run
because `Run` carries no workspace: it links to a task, a project and a principal, and
none of those three reaches a workspace today. Requiring the caller to name a workspace
they are a member of is the strongest gate available at this boundary. Narrowing it to
the run's own workspace is M6 work and needs the missing link built first.

## The exit criterion

§27's exit criterion is *"v0.2 dynamic workflow can use research plugin without
provider-specific logic"*. It runs through one seam:

```text
WorkflowProposal.capability_refs      (validated and authorized by GraphValidator)
        │
        ↓  _materialize_node
WorkflowNodeSpec.capability_refs      (additive, optional, default empty)
        │
        ↓  RunManager._invoke_node_capabilities  (TOOL nodes)
GatewayCapabilityInvoker              (resolve, then execute)
        │
        ↓  CapabilityGateway.execute
EvidenceRecord in the Evidence Store
```

Before M5 the second arrow did not exist. `_materialize_node` constructed
`WorkflowNodeSpec` four times and named `capability_refs` in none of them, so a
capability reference the planner proposed and the validator authorized was dropped on
the floor between validation and execution.

`WorkflowNodeSpec.capability_refs` is **additive and optional**. Every workflow
template persisted before M5 carries no such key, and `WorkflowNodeSpec` is a
`StrictModel` with `extra="forbid"` — a required field here would have rejected every
stored template at once. An empty list is the pre-M5 behaviour exactly: the invoker is
not reached at all, and the TOOL node's diff capture is untouched.

For a `LOOP` node the references land on the **act child**, not the parent. The parent
is a region marker that executes nothing, so hanging capability references from it
would name authority no step ever spends.

Failures are contained: an unresolvable or unauthorized reference returns `None`, one
reference failing does not stop the next, and none of them can change the node's
outcome. A research lookup that cannot be reached must not abort a run that was not
asking for research in the first place.

### ADR3-M5-001 — §10 is the research capability surface, not §9.1

**Status:** accepted, v0.3 M5.

**Context.** The SDD describes the research plugin twice and the descriptions differ.
§9.1 gives a smaller set whose verification-shaped member is
`research.citation.resolve`. §10 gives the larger set, including
`research.citation.verify`. An implementation must pick one, and the capability ids it
picks become registry-stable tokens the moment anything outside this repository binds
to them.

**Decision.** Adopt §10's surface. No aliases for the §9.1 names.

**Why.** §10 is the superset, and the one member on which the two lists genuinely
disagree is the one that decides the criterion. AC3-RES-01 requires the plugin to
expose "citation **verification**". `research.citation.resolve` *resolves*: it looks an
identifier up and returns what the resolver holds. Resolution is a lookup, and a lookup
that succeeds says only that an identifier exists — it does not compare what the
candidate *claimed* against what the resolver *returned*, which is the entire content of
a verification. §9.1's surface therefore cannot satisfy AC3-RES-01 no matter how it is
implemented, because the missing thing is a comparison, not an endpoint. §10 also names
`EvidenceCandidate`, the type §22's Evidence Store consumes, so adopting §10 keeps one
vocabulary across the two sections instead of translating between them.

**Consequences.** `CitationCheck` records both sides of the comparison — the claimed
identifier and the resolved one — so the check is itself evidence about the evidence,
and a disagreement is visible rather than merely reported as a failure.

### ADR3-M5-002 — `github.search` in, `python.execute` out

**Status:** accepted, v0.3 M5.

**Context.** §10 lists six capabilities. One of them, `python.execute`, is arbitrary
code execution offered to a research workflow.

**Decision.** Ship five: the four `research.*` capabilities plus `github.search`. Do not
ship `python.execute`.

**Why.** No M5 criterion needs code execution. AC3-RES-01 asks for literature search,
paper retrieval and citation verification; AC3-RES-02 asks for a backend swap;
AC3-RES-03 asks for provenance; AC3-RES-04 asks for a trust ordering. None of the four
is closer to being met with an interpreter in the capability surface, and every one of
them is harder to reason about with one, because a capability that can run code can
manufacture any evidence it likes and the trust model's whole premise is that a source
cannot set its own trust. `github.search` stays because an implementation survey is a
literature search over code, and it is a read.

**Consequences.** The decision is **enforced, not documented**: a test asserts
`python.execute` is absent from the installed capability set. Re-adding it fails the
suite rather than passing a review.

## Operating it

The plugin is off by default and there are **two** gates, not one:

| Setting | Default | Effect |
|---|---|---|
| `ACCRETION_ENABLE_RESEARCH_PLUGIN` | `false` | Turns the plugin on at all. |
| `ACCRETION_RESEARCH_ALLOWED_HOSTS` | `[]` | Hosts the adapter may reach. Empty means none. |
| `ACCRETION_RESEARCH_CONNECTOR_ID` | `research-openalex` | Which backend the workflow binds to. |
| `ACCRETION_RESEARCH_MAX_RESULTS` | `25` | Upper bound on results per search. |

Enabling the plugin alone opens no egress. That is deliberate: `McpEndpointPolicy`
governs the connection *to* an MCP server and does not inspect what that server then
fetches upstream, so the allowlist is the second gate and it starts empty. See the
deferrals below.

## What M5 deliberately did not build

### No research benchmark

M5's connectors are faked. A benchmark run against them would report the quality of
`tests/fake_research_api.py` — its corpus, its overlaps, its one deliberate identifier
mismatch — and present it as the quality of the research pipeline. That is precisely
the failure mode the acceptance baseline exists to prevent: a number that measures the
fixture and reads as if it measured the system. A research benchmark becomes meaningful
when a real upstream is reachable, which is gated behind
`ACCRETION_RESEARCH_ALLOWED_HOSTS` and is not M5.

### Live plugin health probing — re-deferred to M6

M4 deferred live health probing for installed plugins and M5 defers it again, with the
same reason and one more. The reason: a health probe is only meaningful against a real
endpoint, and every endpoint M5 registers is an in-process fake served over
`ASGITransport`, so a probe would report the health of the test harness. The addition:
probing is a *scheduled* activity, and M5 added no scheduler; wiring one for a single
consumer would put a background loop in the codebase before there is a second thing for
it to run. M6 owns plugin administration and is where both the UI that displays health
and the scheduler that collects it belong.

### Real upstream egress

The research adapter's own outbound calls are not inspected by `McpEndpointPolicy`,
which governs the connection to the MCP server rather than what the server subsequently
fetches. M5 tests entirely offline and no test in the suite touches the network. Before
any production use with a real literature service, real upstream URLs must be gated
behind `ACCRETION_ENABLE_RESEARCH_PLUGIN` **and** a populated
`ACCRETION_RESEARCH_ALLOWED_HOSTS`, and the adapter's egress needs a policy of its own
in the shape of `McpEndpointPolicy`.

### The bundled sample plugin still cannot execute

M4's recorded debt stands: plugin-declared capabilities have no handler seam into
`CapabilityExecutor`, so `accretion-sample-plugin` remains installable and
non-executable in production. M5 is unaffected because research capabilities are MCP
bindings and `RemoteMcpManager` already executes those — but the debt did not go away
and should be scheduled.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| A capability resolves but never executes | Its binding is `enabled = false`. Both backends' bindings exist at all times; only one is enabled. |
| Every record is `UNVERIFIED` | No verifier has run. Trust is relabelled by the verifiers, not at write time. |
| A record has no `trust_score` | Correct, and not a bug: `UNVERIFIED` and `QUARANTINED` records are unrankable by design. |
| `VerifierUnavailableError` for a `research-*` id | The process built a `VerifierRegistry` without `research_verifiers(store)`. The API process assembles it in one place in `api/main.py`. |
| A transform reference will not resolve | The gateway was constructed without `transforms=`. Both the API process and the MCP gateway process pass `default_transform_registry()`. |
| The same paper appears twice | It should not: records are content-addressed per run. Two records mean two different `content_digest` values, so the normalizers disagreed about the content. |
