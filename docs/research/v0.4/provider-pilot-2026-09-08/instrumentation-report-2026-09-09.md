# Fake instrumentation execution report

Status: **FAKE_INSTRUMENTATION_VERIFIED**. Live execution: **NOT_AUTHORIZED**.
Study `ACR-ROUTER-DEV-20260908` began with plan approval on 2026-09-08.
This execution completed on **2026-09-09 at 00:20:23 Asia/Bangkok**
(`2026-09-08T17:20:23.170186+00:00` UTC). It is local instrumentation evidence,
not a new scientific routing result or authorization for a provider pilot.

## Executed scope and outcomes

One clean-source invocation ran three fixed cases serially in disposable Git
repositories/worktrees. The actual existing routing owners froze native
contracts, selected configurations and persisted dispatch claims before
FakeRuntime execution. OutputContractVerifier inspected output independently;
the M3 recorder persisted its attributed three-valued result. Each case used
BASELINE_ONLY, native FALLBACK, Provider.FAKE, LOW_DIGITAL and no selected AGENT
tools. No live adapter, learned scorer, shadow, exploration or promotion ran.

| Case | Runtime terminal | Independent result | Required coverage | Evidence digests | Measurement status |
|---|---|---|---|---|---|
| [Valid output](evidence/2026-09-09/records/valid-output.json) | COMPLETED | PASS | 1.0 | 1 | COMPLETE_FAKE_RECORD |
| [Incorrect output](evidence/2026-09-09/records/incorrect-output.json) | COMPLETED | FAIL | 1.0 | 1 | COMPLETE_FAKE_RECORD |
| [Missing required evidence](evidence/2026-09-09/records/missing-required-evidence.json) | COMPLETED | INCONCLUSIVE | 0.0 | 0 | INCONCLUSIVE |

The last case deliberately withholds the verifier's required-output observation
while preserving the frozen VerificationSpec. It demonstrates missing-coverage
handling. It is not a physical file-disappearance experiment. The FAIL case is
a complete fake measurement of an incorrect artifact; completeness never means
the task passed. These fixture counts are not estimated success or error rates.

## Provenance independent of generated IDs

The generation commit was `cb122b742c33f588be1c2749a085b4078305814a`; its tracked
and untracked source status was empty at export time. The committed evidence is
a byte-for-byte copy of that completed output, added after generation. Native
temporary workspace paths are historical provenance; validation reads the
portable captured artifacts and native JSON, not deleted temporary worktrees.

- [Generation report](evidence/2026-09-09/report.json): source commit/status,
  seven source-file hashes, interpreter and import paths, dependency versions,
  lock/config hashes, UTC completion time and limitations.
- [File manifest](evidence/2026-09-09/manifest.json): SHA-256 identities of all
  **47 other files**; the complete export has 48 files and 92,794 bytes.
  Manifest SHA-256:
  `4cd7298c199ca15d3232d7e5aa8a9772615cfd9ca20a42193332c3a92e00cf7c`.
- [Executed recipe](evidence/2026-09-09/recipe.json): fixed boundaries, no
  automatic retry, new output directory, 3 cases, 1 attempt/case, 1 concurrent
  execution, 30 seconds/case, 120 seconds for the case loop, 65,536 bytes/artifact,
  and 0 provider calls. Each local Git child also has a 15-second bound.

The source module at generation hashed to
`546005645ebb08b5c07d51613491e1200bd9ffb1addbfb6e32d5b5c422d837ed`.
The native contract hashes are integrity seals, not third-party signatures.
Stable file hashes and the recorded source revision retain provenance even
when generated run, session and receipt IDs differ on a later reproduction.

## Environment and checks

Final worker execution and post-review tests used the research worker's own environment,
populated by `uv sync --locked --all-groups`. Observed paths were:

```text
sys.executable = /mnt/data/accretion-v04-execution-2026-09-08/research/.venv/bin/python
accretion.__file__ = /mnt/data/accretion-v04-execution-2026-09-08/research/src/accretion/__init__.py
```

Python was 3.12.9; Pydantic 2.13.4, jsonschema 4.26.0, SQLAlchemy 2.0.52,
pytest 9.1.1 and pytest-asyncio 1.4.0. The recorded lock hash is
`de48b5ada4e50bf9dcfdd48d2f1fff24c4b455b2bb7e9eba7daa32940afdf563`.
Earlier development probes used the main Accretion checkout's dependency
interpreter with `PYTHONPATH=src` pointing at this research source. They are
historical development checks, not the clean execution above.

The exact final invocation from the research worktree was:

```sh
PYTHONPATH=src .venv/bin/python scripts/provider_pilot_dry_run.py --output /mnt/data/accretion-v04-execution-2026-09-08/research-dry-run-2026-09-09
```

The focused post-review suite passed **39 tests in 4.50 seconds** using:

```sh
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=src .venv/bin/python -m pytest -p pytest_asyncio.plugin tests/test_provider_pilot_dry_run.py -q
PYTHONPATH=src .venv/bin/python -m mypy src/accretion/provider_pilot.py
PYTHONPATH=src .venv/bin/python -m ruff check src/accretion/provider_pilot.py scripts/provider_pilot_dry_run.py tests/test_provider_pilot_dry_run.py
PYTHONPATH=src .venv/bin/python -m ruff format --check src/accretion/provider_pilot.py scripts/provider_pilot_dry_run.py tests/test_provider_pilot_dry_run.py
```

Ruff, format and mypy passed. Schema checks accepted 3 illustrative fixtures
and rejected all 10 declared structural mutations. The focused suite additionally
proved the following boundaries with executed negative witnesses:

| Witness | Evidence in committed source |
|---|---|
| Hosted/learned/budget/promotion constructors and network connections trapped | [Test module](../../../../tests/test_provider_pilot_dry_run.py), lines 36–53 |
| AUTO, SHADOW, EXPLORE, hosted provider, AGENT tool and ambient opt-in refused before runtime construction | Same test module, lines 89–134 |
| Native contracts, model/runtime/session/lease/terminal/dispatch joins, schema and evidence checks reject contradictions | [Validation implementation](../../../../src/accretion/provider_pilot.py), lines 550–748; tests, lines 137–275 |
| No producer self-acceptance or empty-evidence completeness | Test module, lines 239–261 |
| Whole-case timeout actually interrupts setup, one attempt only; existing output cannot be overwritten | Test module, lines 278–311; [timeout implementation](../../../../src/accretion/provider_pilot.py), lines 751–798 |

An independent agent reviewed the final export read-only: the unmodified
archive validated, and all four previously accepted contradictory-field probes
(model, runtime version, deterministic flag, execution status) now raised
`PilotRefused`. It found no remaining blocker within this fake-only scope.
That review did not execute another dry run or modify the archive.

The source finalizer and the subsequent copied-archive validation both checked
the full file manifest, JSON schema, native CanonicalContract seals and exact
record joins. The validator reconstructs the independent result from saved
verifier inputs and compares its evidence, coverage and independence against
the native result; it does not trust exported booleans alone. Independent result
timestamps are captured after verifier completion.

All 48 archive files were also materialized from the Git index into a fresh
temporary directory and validated there. The three fixed fake artifact files
were explicitly staged despite the repository's general `artifacts/` ignore
rule; portable validation does not depend on ignored working-tree files.

To validate the committed archive without executing a case:

```sh
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
from accretion.provider_pilot import validate_export
print(validate_export(Path('docs/research/v0.4/provider-pilot-2026-09-08/evidence/2026-09-09'))['status'])
PY
```

## Limits and next gate

Provider calls, external tokens and provider charge were zero by construction;
the cost basis is `NO_PROVIDER_CALLS`, with currency null. No price or monetary
rate was supplied or measured. The elapsed field covers the pre-submit request
preparation through independent-result export preparation; it excludes routing
and worktree setup and final export validation. Local compute is null rather
than an invented split, and total priced measurement remains unsupported.

This is one manually orchestrated AGENT node per case using MemoryStore. It
does not verify the full production scheduler/feedback path, restart behavior,
database durability, provider setting fidelity, monetary accounting or safety
of the excluded AUTO/shadow/exploration paths. It does not qualify a scientific
verifier or turn the verifier's embedded risk estimate into a measured error
rate. The coordinator's [closure record](../../../releases/v0.4/closure-execution-2026-09-08.md)
records passing integrated engineering checks and a separate fresh fake run at
`f8b47a8`. The original worker source and archive above remain unchanged.

The old preregistration, amendment and access-log hashes remain unchanged;
the log still has four existing rows, and this work added zero locked accesses.
The documentation check also confirmed all 187 imported future-package files
unchanged. This worker preparation read no locked or drift trace rows and ran
no old evaluation. The coordinator's ordinary regression reproduction is
separately scoped in the closure record.

[Readiness](readiness.json) now records only the fake instrumentation gates as
verified. Provider/accounts, qualified evaluator, task inventory, configurations,
scientific parameters, price provenance, currency, monetary/call/time ceilings
and the exact live protocol still need the inputs and gates defined in
[the draft protocol](protocol.md). Live execution remains **NOT_AUTHORIZED**.
