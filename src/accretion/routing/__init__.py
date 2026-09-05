"""The v0.4 routing layer: what may be built, and — later — what should be.

**What lives here in v0.4.** By the end of the release this package holds the whole
deterministic router: the node-contract and verification-spec freeze (M2.1), candidate
construction and the hierarchical selector (M2.2), the shared ``NodeRoutingService`` and
``FeedbackPipeline`` protocols (M1.2), policy gates (M1.2), the offline ranker's calling
convention (M4), the project adapter (M5), shadow evaluation (M6) and the guarded bandit
(M7). Every one of those is *selection*: choosing among configurations that are already
known to be admissible.

**What M1 owns, and only that.** Admissibility itself. Three modules, and no more:

* :mod:`accretion.routing.reasons` — the closed, versioned vocabulary of reasons a
  decision may give.
* :mod:`accretion.routing.snapshot` — :class:`~accretion.routing.snapshot.RoutingSnapshot`,
  an immutable picture of the registry, the runtimes, the connections and the policy at one
  instant, identified by four digests that never contain a secret.
* :mod:`accretion.routing.compatibility` — the deterministic rules that turn a snapshot and
  a subject into a :class:`~accretion.contracts.routing.CompatibilityDecision`.

Two properties hold across all three and are what the rest of the release is built on.
The engine is **pure**: it reads a snapshot and returns decisions, never touching the store,
so a caller decides what to persist and a test can replay a decision without a database.
And ``UNKNOWN`` is **never** compatible — SDD §7.7 makes that a MUST, and eligibility for a
required constraint is ``status is CompatibilityStatus.COMPATIBLE`` and nothing else.
"""

from __future__ import annotations
