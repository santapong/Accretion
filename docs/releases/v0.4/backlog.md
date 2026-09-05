# v0.4 prioritized backlog

Status: **M0 in progress.** The normative contract is [SDD v0.4](../../sdd/Accretion_SDD_v0.4.md);
its §19 orders the milestones and its §20 owns the criteria. This ledger records status only.

## Delivery order

| Priority | Milestone | Owns (SDD §20) | Status |
|---:|---|---|---|
| 1 | M0 contract and feature freeze | none (ADR-052) | delivered (#123) ([plan](m0-plan.md), [freeze record](m0-freeze.md)) |
| 2 | M1 compatibility engine | 005-008 | not started |
| 3 | M2 hierarchical deterministic selector | 001, 002, 004, 009-015, 022 | not started |
| 4 | M3 experience and feedback pipeline | 003, 023-034 | not started |
| 5 | M4 offline ranker and calibration | 016 | not started |
| 6 | M5 project adapter and cold start | 021 | not started |
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
| `experience/embedding.py:46` `canonical_digest` | **converged** | Already passed `ensure_ascii=False`, so it agrees with `canonical_json` byte for byte on *every* payload `json.dumps` would accept, not merely the committed ones. Its persisted digests (`ExperienceEmbedding.input_digest`, segment `content_digest`, the three bundled plugin manifest digests) cannot move. |
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

## Parked beside v0.4

The v0.3.1 operator-UI redesign (M9 of the v0.3 ladder) is parked after its stylesheet port
completed; its remaining steps (Preflight, projection store, cosmic scene, orbit, dashboard,
release) resume from their plan when the owner reopens it.
