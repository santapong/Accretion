"""Plugin manifest, trust, and dependency foundations for the v0.3 M4 plugin manager.

ADR3-006 / SDD 9.4 governs every module here: *plugin manifests are requests; policy
is authority*. Nothing in this package grants a plugin anything; it parses, digests,
authenticates, and resolves declarations so the governance layer can decide.
"""
