"""Content-addressed storage for the bytes a router version points at.

:class:`~accretion.contracts.routing.RouterModelVersion` is a *record*, not a file: it
carries an ``artifact_digest`` and a ``calibration_artifact_digest`` and says nothing about
where those bytes live. ADR-049 makes promotion reversible, which only means anything if the
bytes a retired version named are still retrievable and still the bytes it named. This module
is the retrieval half of that promise.

**Why the digest is the name.** A path is a mutable pointer and a digest is not, so an
artefact keyed by digest cannot be silently replaced by a redeploy, a partial write or a
hand. :meth:`ArtifactStore.load` rehashes what it read before returning it, so a caller
that asked for one model can never be handed another one's numbers under the version id
that promised these.

**Why bytes and not objects.** :mod:`accretion.routing.ranker` already owns the artefact
*directory* — :meth:`~accretion.routing.ranker.LearnedOutcomePredictor.save` writes a
manifest, a ranker and a calibration under a directory named for the manifest's digest, and
verifies each part on load. That layout answers "here is one assembled predictor". It
cannot answer the question :class:`RouterModelVersion` actually asks, which is "here are
*two independently versioned* digests, plus the evaluation documents that justify them",
because §7.12 and OQ-405 keep the trees and the calibration apart precisely so that
recalibrating without retraining is a new version rather than an edit. So this store is
deliberately dumb: one flat blob per digest, and the *record* is what assembles them. The
two layouts coexist without colliding — a blob lives at ``root/<2-char shard>/<digest>``
and a ranker directory at ``root/<digest>/``, and a 64-character digest is never a
two-character shard.

The root is injected. :meth:`ArtifactStore.default` reads
``ACCRETION_ROUTER_ARTIFACT_DIR`` through :class:`~accretion.config.Settings` and falls
back to ``.accretion/router-artifacts``, which is inside the gitignored data directory:
an artefact tree that landed under version control would make every training run a diff.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from accretion.config import get_settings
from accretion.routing.ranker import ArtifactDigestMismatchError, ArtifactNotFoundError

_SHARD = 2
"""Characters of the digest used as a subdirectory, so one directory never holds every blob."""


def default_artifact_root() -> Path:
    """Where artefacts live when nothing injected a root: the ``ACCRETION_`` setting."""

    return get_settings().router_artifact_dir


@dataclass(frozen=True, slots=True)
class ArtifactStore:
    """A flat, content-addressed byte store rooted at one directory.

    Frozen: a store whose root could be reassigned would let a version's digest resolve to
    different bytes depending on when it was asked, which is the one property the digest
    exists to deny.
    """

    root: Path

    @classmethod
    def default(cls) -> ArtifactStore:
        """The store the configured root names."""

        return cls(default_artifact_root())

    def path_for(self, digest: str) -> Path:
        """Where ``digest`` is or would be written."""

        return self.root / digest[:_SHARD] / digest

    def save(self, payload: bytes) -> str:
        """Write ``payload`` under its own SHA-256 and return that digest.

        Writing an artefact that is already stored is a no-op rather than an error: the
        bytes are equal by construction — the name *is* their digest — so a second write
        of the same training result is idempotent and a re-run costs nothing.
        """

        digest = hashlib.sha256(payload).hexdigest()
        target = self.path_for(digest)
        if target.exists():
            return digest
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written beside the target and renamed, so a reader never observes a half-written
        # blob under a name that promises a digest the bytes do not yet have.
        staging = target.with_name(f"{digest}.partial")
        staging.write_bytes(payload)
        staging.replace(target)
        return digest

    def load(self, digest: str) -> bytes:
        """Read the artefact named ``digest``, rehashing it before returning it.

        Raises :class:`~accretion.routing.ranker.ArtifactNotFoundError` when nothing is
        stored under that name and
        :class:`~accretion.routing.ranker.ArtifactDigestMismatchError` when what is stored
        does not hash to it. The second is not a corruption report to be repaired: bytes
        that no longer match their name are somebody else's artefact wearing this one's
        label, and predicting from them would attribute their numbers to this version.
        """

        target = self.path_for(digest)
        try:
            payload = target.read_bytes()
        except FileNotFoundError as error:
            raise ArtifactNotFoundError(f"no artefact stored under digest {digest}") from error
        actual = hashlib.sha256(payload).hexdigest()
        if actual != digest:
            raise ArtifactDigestMismatchError(
                f"artefact at {target} hashes to {actual}, not the requested {digest}"
            )
        return payload
