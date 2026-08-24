# P7 verified-experience decisions

Status: frozen for v0.2 implementation  
Date: 2026-08-24  
Scope: P7 retrieval and trajectory replay only

P7 adds operator-controlled reuse of verified local experience. It does not add
automatic learning, cross-repository transfer, autonomous policy changes, or new
execution authority. This record is the implementation boundary: a benchmark
failure leaves P7 incomplete and must not be repaired by weakening these rules.

## Feature boundary and authority

- Global `ACCRETION_ENABLE_EXPERIENCE_RETRIEVAL` defaults to `false`.
- A project must opt into `experience_retrieval`; that setting requires both P5
  dynamic workflows and P6 candidate search.
- Operators explicitly materialize terminal evidence, select at most three
  retrieved matches, and attach replay to a P6 search.
- Retrieval is advisory. It cannot add workflow topology, capabilities,
  verifiers, approval gates, permissions, or side-effect authority.
- Retrieval is restricted to one repository. Repository identity is the SHA-256
  digest of the normalized `remote.origin.url`, with a project-ID-derived digest
  only when no Git remote exists.

## Sources, trust, and safe content

Only terminal runs and terminal P6 candidates may be materialized.

| Evidence | Trust | Use |
| --- | --- | --- |
| Successful with every required verifier passing and complete artifacts/policy evidence | HIGH | Positive retrieval and eligible replay seed |
| Complete failed, requires-human, or out-ranked candidate with a failure taxonomy | MEDIUM | Negative avoidance guidance only |
| Incomplete or inconclusive | LOW | Excluded |
| Cancelled | none | Rejected at materialization |

An experience contains only deterministic procedural segments:
`WORKFLOW_PATH`, `TOOL_SEQUENCE`, `VERIFIER_FINDINGS`, `REPAIR_PATTERN`,
`FAILURE_PATTERN`, and `ARTIFACT_SHAPE`. Raw patches, transcripts, capability
arguments/results, credentials, session/native payloads, and side-effect IDs are
never experience content.

## Deterministic representation and storage

PostgreSQL 16 uses pgvector `VECTOR(384)`. P7 uses exact cosine search and creates
no HNSW or IVFFlat index. The pinned development and CI image is
`pgvector/pgvector:0.8.6-pg16` and the Python integration is `pgvector>=0.5,<0.6`.

`deterministic-hybrid-384-v1` applies Unicode NFKC normalization and existing
secret redaction, then signed SHA-256 feature hashing with these frozen weights:

- task family and type: 3;
- profile buckets, verifiers, manifests, outputs, and transfer tags: 2;
- redacted objective, constraints, and success-criterion unigrams/bigrams: 1.

The vector is L2-normalized. Persistence stores the vector, algorithm version,
and normalized-input digest, not the normalized source text.

## Retrieval and compatibility

Retrieval happens after deterministic task profiling and before a P5 proposal.
Selection creates an immutable `ContextBundle` v2 revision. Selection freezes as
soon as a workflow proposal exists.

Hard rejection applies to retracted evidence, repository mismatch, missing or
non-ancestor source commits, incompatible architecture major versions, invalid
digests, incompatible policy/verifier state, unavailable or denied referenced
skills/plugins/capabilities, and irreversible or protected side-effect state.

Accepted evidence is scored as follows:

- commit compatibility: same `1.0`, ancestor `0.9`, otherwise reject;
- manifest compatibility: exact `1.0`, same changed files `0.8`, manifest-set
  drift `0.6`;
- environment: `0.6 × commit + 0.4 × manifest`;
- version: `0.35 × architecture + 0.30 × policy/verifier + 0.20 ×
  runtime/provider/model + 0.15 × prompt/context/tool`;
- freshness: at most 30 days `1.0`, 90 days `0.95`, 180 days `0.9`, older `0.8`;
- semantic: `clamp(1 - cosine_distance, 0, 1)`;
- final score: geometric mean of semantic, environment, version, and freshness;
- transfer risk: `1 - min(environment, version, freshness)`.

Positive replay eligibility requires HIGH trust and semantic `>= 0.60`,
environment `>= 0.90`, version `>= 0.85`, freshness `>= 0.80`, and final
`>= 0.75`. Negative guidance requires MEDIUM complete failure evidence and
semantic `>= 0.55`, environment `>= 0.75`, and final `>= 0.65`; it can never seed
a branch. Accepted, down-ranked, and rejected results persist their component
scores and reasons.

## Replay execution and recovery

`REPLAY_BRANCH` always creates candidate 1 as a fresh control and candidates 2–4
from one explicitly selected positive match each. A search plan carries one to
three `replay_seed_match_ids`, zero to three `negative_guidance_match_ids`, and a
branch count of `1 + positive seeds`. Other search modes forbid those fields.

Each replay branch starts from the same P6 parent snapshot in a new worktree and
session. A `TrajectorySeed` contains only controlled procedure, assumptions, and
required revalidation. Compatibility is checked when planning, launching, and
selecting/promoting. Invalid evidence records rejection and prunes that candidate;
the fresh control continues and no substitute seed is chosen.

Replay never reuses patches, sessions, credentials, permissions, approvals,
side-effect identifiers, acceptance results, or mutable workspace state. It
inherits P6 shared budgets, cancellation, isolated promotion, and recovery.
Interrupted branches are not automatically rerun. P7 events use monotonic
target-run sequences and explicit causation:
`EXPERIENCE_QUERY`, `EXPERIENCE_RETRIEVED`, `TRAJECTORY_REPLAY_STARTED`, and
`TRAJECTORY_REPLAY_REJECTED`.

## Frozen benchmark gate

The held-out suite contains 20 tasks (five each for IMPLEMENT, REVIEW, ANALYSIS,
and RESEARCH), 50 sources (20 positive, 10 negative, 20 stale/incompatible), and
80 traces covering fresh, success-only, success-plus-failure, and replay
treatments for every task.

Negative transfer means the fresh control succeeds while a treatment fails, or
the treatment quality is at least `0.05` below fresh. P7 passes only when:

- false accepts do not increase;
- stale/incompatible rejection is at least 95%;
- negative transfer is at most 5%; and
- mean quality improves by at least `0.03`, or tool calls fall at least 10%
  without a success-rate regression.

Reports include success, quality, turns, tool calls, latency, uplift, compute,
false accepts, negative transfer, stale accuracy, experience use/rejection/null
rates, and frozen corpus/trace hashes. The benchmark runs from replay fixtures
only. A failed gate is reported unchanged; thresholds and data are not tuned.

## Explicit exclusions

P7 does not implement automatic capture or selection, automatic trust changes,
policy or skill mutation, reinforcement learning, cross-repository or cloud
experience, raw transcript retrieval, or release/tag automation.
