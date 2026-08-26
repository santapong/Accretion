from __future__ import annotations


class PluginManagerError(RuntimeError):
    """Base class for every plugin lifecycle failure.

    Exception handlers must register this class *last*, so the specific subclasses
    below keep their own status codes.
    """


class PluginManifestError(PluginManagerError):
    """The manifest is absent, unparseable, or violates a package shape rule."""


class PluginSignatureError(PluginManagerError):
    """A digest pin or detached signature did not verify against the manifest."""


class PluginTrustError(PluginManagerError):
    """The package cannot reach the trust level its risk profile requires."""


class PluginDependencyError(PluginManagerError):
    """A version constraint is unsatisfiable or the dependency graph has a cycle."""


class PluginPolicyDenied(PluginManagerError):
    """Policy refused a declaration; the plugin never gains the authority."""
