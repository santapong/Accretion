# M2 host construction probe

Scope: bounded local isolation construction, not a simulation episode, adapter
conformance or composite AC5 result. Follow the
[runtime boundary note](../../m2-runtime-boundaries.md).
The [manifest](manifest.json) hashes the preserved source and result files.

The [probe image](Dockerfile) pins an existing Python image by immutable digest
and installs the [fixed probe source](probe.py.txt). The resulting image is
`sha256:297234df2302c6230fdf021f396732f66aeabe39c9fa72b013865cd526bb3365`.
It contains no robot model, adapter or operational approval. To rebuild, copy the
preserved `probe.py.txt` bytes to `probe.py` alongside the Dockerfile. The source
was retained verbatim, including its original formatting.

The [first observed run](083301-record.json) passed at 08:33 UTC on 9 September
2026. Its [raw probe output](083301-probe.json) records nonroot UID, zero
effective capabilities, no-new-privileges and seccomp filtering. Network egress
failed with ENETUNREACH; image/bootstrap writes failed with EROFS; the one-MiB
temporary filesystem reached ENOSPC. Observed cgroup settings were 0.5 CPU,
128 MiB RAM, zero swap and 16 PIDs; process CPU/core/open-file rlimits matched.
The probe forked a child. Container kill, stopped PID zero, container removal and
the local attach process's expected exit 137 were observed.

A [subsequent preparation refusal](083721-record.json) found that this Docker
version omits the empty optional Sysctls field. The guard was corrected to
accept absence/empty values while still rejecting configured overrides. No
entrypoint started during that refused preparation. The
[final run](083924-record.json) then passed again with the strengthened inventory,
environment/rlimit/protected-path checks and immutable lease witness bytes;
[raw output](083924-probe.json) is retained. Source file hashes identify each
tested construction precisely; these are not hashes of an entire release.

The daemon and complete container inspection bytes remain in the local task
workspace. This portable bundle retains the scoped probe results, source and
exact source hashes, not an unreviewed dump of the daemon's entire configuration.
The regular tests additionally replay a scoped created-container inspection and
mutate isolation fields to prove refusal, including under Python optimization.

No CPU-budget, orphan-watchdog, restart recovery, simulator safety or independent
verification completion is inferred from this probe. Those remain explicit
runtime integration obligations.
