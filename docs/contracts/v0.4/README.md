# v0.4 contract schemas

Twenty-one JSON Schema 2020-12 documents, one per contract in the v0.4 family, generated
from the pydantic models in [`src/accretion/contracts/routing.py`](../../../src/accretion/contracts/routing.py)
and **committed**. They are the machine-readable half of the M0 contract freeze (SDD v0.4
§19; cross-release registry §19, §20 decision 1).

Nineteen of them were frozen by M0. The last two — `ShadowRolloutResult.schema.json` and
`RouterActivation.schema.json` — arrived with the **freeze delta of 5 Sep 2026**
(ADR-060, ADR-061), which also re-sealed `ObjectiveContract.schema.json` for the additive
`exploration_policy` field (OQ-410, ADR-062). The delta and the digests it moved are
recorded in [`docs/releases/v0.4/m0-freeze.md`](../../releases/v0.4/m0-freeze.md); the
freeze rule below applies to all twenty-one identically.

## What they are

Each file is `<Model>.schema.json` — the class name, not the module path — and describes the
**validation** view of the model: what a reader must accept, with every nested value object
inlined under `$defs`. The registry §3 canonical header appears in all twenty-one, because
every v0.4 contract inherits it:

| Field | Meaning |
|---|---|
| `contract_type` | `const`-pinned canonical type string |
| `schema_version` | semver; an unknown **major** is rejected, not upcast (registry §3.2) |
| `contract_id` | prefixed base32 id (ADR-055), or an owner-minted label where ADR-055 mints no prefix |
| `content_hash` | ADR-056 digest of the document with this field excluded; optional on input, always present after parsing |
| `created_at`, `created_by`, `workspace_id`, `project_id` | provenance and tenancy |
| `supersedes_contract_id`, `objective_contract_ref`, `labels`, `retention_class` | the optional shared header fields |

`content_hash` is optional in the schema and required in practice: a document that omits it
is sealed on construction, and a document that carries a *wrong* one is rejected. The schema
cannot express "must equal the digest of the rest of this document", which is why the golden
fixtures under [`tests/fixtures/contracts/v0.4/`](../../../tests/fixtures/contracts/v0.4)
carry real digests and the tests recompute them.

## How to regenerate

```bash
uv run --no-sync python scripts/export_contract_schemas.py           # write
uv run --no-sync python scripts/export_contract_schemas.py --check   # verify
```

The output is deterministic — sorted keys, two-space indent, trailing newline — so
regenerating on any machine produces byte-identical files. `--check` exits non-zero naming
the first model whose committed file differs, and `tests/test_v04_m0_schemas.py` makes the
same comparison per model so that a forgotten regeneration is a red test rather than a
surprise in CI.

The golden fixtures have their own generator:

```bash
uv run --no-sync python scripts/export_contract_fixtures.py
```

## The freeze rule

**A change to any file in this directory is a versioned contract migration, not an edit.**

Registry §17 requires nine things of every contract migration: an owner and affected-release
list, a semantic diff with authority and safety impact analysis, forward *and* backward
compatibility tests, fixture migration and round-trip tests, an event-consumer compatibility
test, replay against historical accepted and failed records, a policy and training-snapshot
impact analysis, a rollback plan, and human approval for any change to authority, objective,
verifier, evidence-class or physical semantics.

Registry §3.2 fixes what the version must do:

| Change | Version effect |
|---|---|
| Add an optional field with a defined default | Minor |
| Add an enum value | Minor only if readers fail safely |
| Clarify documentation with no semantic change | Patch |
| Remove or rename a field | **Major** — readers reject an unknown major |
| Change authority, verification, safety, unit or identity semantics | **Major plus migration review** — fail closed |
| Make an optional field required | **Major** |

No migration may make a denied action allowed, convert `FAIL` or `INCONCLUSIVE` to `PASS`, or
convert simulation evidence to physical evidence. Historical records are never rewritten in
place; a migrated projection references the original record and its migration receipt.

Until M8 — the first milestone with a second writer — read-boundary upcasting (registry
§20.5) does not exist, `extra="forbid"` stands, and an unknown major is refused outright
(ADR-057).
