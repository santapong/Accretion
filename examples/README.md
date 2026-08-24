# Accretion examples

Examples use only public interfaces and default to the deterministic fake runtime.
They are designed to be safe on a local checkout and must not require a signed-in
provider session.

## Deterministic showcase

Start the database and API from the repository root:

```bash
make dev-db
make migrate
make api
```

In another terminal, run:

```bash
uv run python examples/showcase.py --repository "$PWD"
```

The script registers the checkout, creates a bounded read-only review task, shows
the deterministic strategy choice, executes it with the fake provider, waits for
a terminal state, and prints a compact summary from the durable audit endpoint.

See the [showcase walkthrough](../docs/guides/showcase.md) for the visual flow and ways
to inspect the run.
