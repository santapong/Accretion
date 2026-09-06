"""The versioned reason catalogue every compatibility decision draws from (SDD §7.7).

A :class:`~accretion.contracts.routing.CompatibilityDecision` carries a ``reason_code``
constrained only to ``^[A-Z][A-Z0-9_]*$``. That pattern stops a decision from carrying a
sentence, and nothing else: two rules could spell the same refusal ``NO_CONNECTION`` and
``CONNECTION_MISSING`` and both would be accepted, at which point the codes stop being a
vocabulary and become free text in upper case. So the vocabulary lives here, closed, and the
engine may emit nothing that is not in it.

**Why the catalogue is versioned.** :data:`RULE_VERSION` is written into every decision's
``rule_version`` field and into the digest its ``contract_id`` derives from. A decision
replayed in a year is only explicable against the exact rules that produced it, and "the
rules changed" has to be a *visible* event rather than a silent re-interpretation of stored
receipts. Adding a code, removing one, or changing what an existing one means all bump this
string; the old decisions keep pointing at the old version, and a reader that does not know
that version knows that it does not know.

**What was lifted from P7, and what was left behind.** The v0.2 experience engine
(``ExperienceService.assess``) already computes hard-incompatibility reasons, and registry
§21 makes "the same concept under a different name" a stop-and-reconcile event, so the codes
that grade a *candidate configuration* are taken from it verbatim rather than re-spelled.
The ones that grade a *past experience* are deliberately not lifted; each is named in
:data:`_P7_CODES_NOT_LIFTED` below with the reason. The distinction is the same one ADR-054
(c) draws between ``CompatibilityAssessment`` and ``CompatibilityDecision``: one asks "how
usable is this memory", the other asks "may this tuple be built at all".
"""

from __future__ import annotations

from enum import StrEnum

RULE_VERSION = "compat-rules/1"
"""The version of this catalogue *and* of the rule bodies that emit from it.

One string covers both because they cannot move independently: a rule whose meaning changed
without changing its code would produce decisions that replay to the wrong explanation, and a
code whose meaning changed is a rule change by another name.
"""


class ReasonCode(StrEnum):
    """Every reason a v0.4 compatibility decision may give, and nothing else.

    A ``StrEnum`` rather than a tuple of bare constants so that a typo is an
    ``AttributeError`` at import time rather than a decision carrying a code no reader
    recognises. Members are compared with ``is``; their ``.value`` is what reaches the
    contract, because ``CompatibilityDecision.reason_code`` is typed ``str`` and a persisted
    row should hold a plain string.
    """

    # The positive decision. Present as a code rather than left implicit because
    # `reason_code` is required on every decision, and a compatible verdict that had to
    # invent a filler string would fork into as many spellings as there are rules.
    COMPATIBLE = "COMPATIBLE"

    # ---------------------------------------------------------------------------------
    # Lifted verbatim from the v0.2 P7 engine (`experience/service.py`), because these
    # nine grade a configuration and not a memory.
    # ---------------------------------------------------------------------------------
    CAPABILITY_DENIED = "CAPABILITY_DENIED"
    """The capability appears on an explicit deny list, so no grant can admit it."""

    CAPABILITY_NOT_ALLOWED = "CAPABILITY_NOT_ALLOWED"
    """The capability is not on the allow list this task was authorised with."""

    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    """The capability is known to the registry but nothing in this snapshot can serve it."""

    POLICY_INCOMPATIBLE = "POLICY_INCOMPATIBLE"
    """The policy in force differs from the one this subject was admitted under."""

    PROTECTED_SIDE_EFFECT_STATE = "PROTECTED_SIDE_EFFECT_STATE"
    """The subject would touch protected side-effect state that routing may not enter."""

    SKILL_OR_PLUGIN_UNAVAILABLE = "SKILL_OR_PLUGIN_UNAVAILABLE"
    """The skill is neither a registered skill nor an allow-listed plugin in this snapshot."""

    VERIFIER_INCOMPATIBLE = "VERIFIER_INCOMPATIBLE"
    """The bound verifier cannot enforce what this node requires to be verified."""

    VERIFIER_UNAVAILABLE = "VERIFIER_UNAVAILABLE"
    """The bound verifier is not registered in the verifier registry this snapshot saw."""

    ARCHITECTURE_MAJOR_INCOMPATIBLE = "ARCHITECTURE_MAJOR_INCOMPATIBLE"
    """The subject declares a different architecture major than this reader understands."""

    # ---------------------------------------------------------------------------------
    # New in M1.2, and the one code the M1.1 pre-declaration below missed. It has no P7
    # counterpart because P7 never asked an authority anything: it graded memories, and a
    # memory cannot be sent for approval. See the note under `_P7_CODES_NOT_LIFTED` for why
    # adding it here did not bump `RULE_VERSION`.
    # ---------------------------------------------------------------------------------
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    """The policy engine returned ``REQUIRE_APPROVAL``, and routing does not pre-approve.

    A gate that treated ``REQUIRE_APPROVAL`` as a soft yes would hand the router the one
    authority decision SDD §5.3 reserves for a human: the approval is *content-bound*, so it
    can only be granted against a request that exists, and no request exists while a
    configuration is still being chosen. INCOMPATIBLE is therefore the honest verdict, and
    this code is what tells an operator that the refusal is an unmade decision rather than a
    denial — the two need different actions and would otherwise share ``CAPABILITY_DENIED``.
    """

    # ---------------------------------------------------------------------------------
    # New in v0.4. Each names a refusal the P7 vocabulary had no word for, because P7
    # never looked at runtimes, connections, MCP servers or a frozen verification spec.
    # ---------------------------------------------------------------------------------
    CAPABILITY_DISABLED = "CAPABILITY_DISABLED"
    """The capability, its bindings, or the plugin behind it are switched off."""

    CONNECTION_REQUIRES_REAUTH = "CONNECTION_REQUIRES_REAUTH"
    """A connection exists but its status or granted scopes demand re-authorisation."""

    MCP_SERVER_NOT_READY = "MCP_SERVER_NOT_READY"
    """The MCP server behind this binding is absent, disabled, or circuit-broken."""

    SCOPE_INSUFFICIENT = "SCOPE_INSUFFICIENT"
    """The connection is usable but lacks a scope the requirement demands."""

    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
    """No runtime in this snapshot matches the tuple, or the matching one is not READY."""

    RUNTIME_VERSION_OUT_OF_RANGE = "RUNTIME_VERSION_OUT_OF_RANGE"
    """The runtime is READY but its observed version falls outside the pinned range."""

    VERIFIER_SPEC_HASH_MISMATCH = "VERIFIER_SPEC_HASH_MISMATCH"
    """The verifier binding enforces a different verification spec than the node pins."""

    ENVIRONMENT_CONSTRAINT_UNMET = "ENVIRONMENT_CONSTRAINT_UNMET"
    """A declared environment constraint evaluates false against this configuration."""

    COMPATIBILITY_UNKNOWN = "COMPATIBILITY_UNKNOWN"
    """The rule could not decide; SDD §7.7 forbids reading this as compatible."""


# Which milestone emits what. M1's compatibility rules emit COMPATIBLE, CAPABILITY_DISABLED,
# CAPABILITY_UNAVAILABLE, COMPATIBILITY_UNKNOWN, CONNECTION_REQUIRES_REAUTH,
# ENVIRONMENT_CONSTRAINT_UNMET, MCP_SERVER_NOT_READY, RUNTIME_UNAVAILABLE,
# RUNTIME_VERSION_OUT_OF_RANGE, SCOPE_INSUFFICIENT, SKILL_OR_PLUGIN_UNAVAILABLE,
# VERIFIER_SPEC_HASH_MISMATCH and VERIFIER_UNAVAILABLE. The remaining five —
# CAPABILITY_DENIED, CAPABILITY_NOT_ALLOWED, POLICY_INCOMPATIBLE,
# PROTECTED_SIDE_EFFECT_STATE and ARCHITECTURE_MAJOR_INCOMPATIBLE, plus
# VERIFIER_INCOMPATIBLE — are authority and semantics rather than availability, and are
# emitted by M1.2's policy gates and M2's candidate builder. They are declared here rather
# than added later because `RULE_VERSION` covers the whole catalogue: adding a code in M1.2
# would bump the version of rules whose meaning had not changed, and every decision M1
# persisted would then point at a version string no longer in use.
#
# M1.2 nevertheless had to add one — APPROVAL_REQUIRED — because the pre-declaration above
# enumerated the codes M1.2's gates would *refuse* with and forgot the one they *defer*
# with. `RULE_VERSION` stays `compat-rules/1` anyway, and the reason is narrow rather than
# convenient: the version exists so that a persisted decision remains explicable against the
# rules that produced it, and no decision under `compat-rules/1` exists outside this
# repository's tests. M1 is the first milestone that emits one at all and it has not shipped,
# so there is no reader pointing at the old catalogue to mislead. The rule stands unchanged
# for every later addition: once v0.4 ships, a new code bumps the version.

ALL_REASON_CODES: tuple[str, ...] = tuple(code.value for code in ReasonCode)
"""Every code in declaration order, as plain strings, for the golden test.

Exported as strings rather than members so the golden test can compare against a literal
tuple it wrote out by hand: a test that imported the enum and compared it to itself would
pass through any rename.
"""


_P7_CODES_NOT_LIFTED: tuple[str, ...] = (
    # Each of these grades a *past experience* rather than a candidate configuration, which
    # is exactly the distinction ADR-054 (c) keeps between `CompatibilityAssessment` and
    # `CompatibilityDecision`. Lifting them would give the routing engine words for
    # judgements it is structurally unable to make, and a code no rule can emit is a code
    # somebody eventually emits by hand.
    "EXPERIENCE_RETRACTED",
    # A retraction is a fact about a stored memory. A configuration cannot be retracted;
    # it is constructed fresh for every routing request.
    "FAILURES_EXCLUDED",
    # Says the *query* asked not to see negative experiences. It describes a retrieval
    # filter, not an admissibility property of a tuple.
    "INVALID_DIGEST",
    # Guards digests recorded on an experience row. A v0.4 contract's digests are sealed
    # and verified by `CanonicalContract`, so a malformed one never reaches a rule.
    "REPOSITORY_MISMATCH",
    # Compares the repository an experience was recorded in against the querying one.
    # Routing decides for the run in hand; there is no second repository to disagree with.
    "SKILL_NOT_REQUESTED",
    # Rejects a memory that used a skill the current task did not request. The
    # configuration-side question — is this skill available at all — is
    # `SKILL_OR_PLUGIN_UNAVAILABLE`, which *is* lifted.
)
"""The five P7 codes deliberately left in P7, each with the reason it stayed there."""
