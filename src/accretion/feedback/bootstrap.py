"""Application assembly for the M3 feedback pipeline.

The twin of :mod:`accretion.routing.bootstrap`, and deliberately the same shape: one function
that turns the objects an application already has — a
:class:`~accretion.services.run_manager.RunManager` and an
:class:`~accretion.experience.service.ExperienceService` — into the service the run manager will
call, so that neither the API lifespan nor the MCP gateway has to know how the pipeline is put
together.

Only the two collaborators, and both are already constructed by whoever calls this. The store,
the operator identity and the runtimes all come off the manager, which is what makes this the
*only* place the pipeline's dependencies are decided: an M3b wiring line that built a
``DefaultFeedbackPipeline`` by hand would be a second such place, and the two would drift the
first time an argument was added.
"""

from __future__ import annotations

from datetime import UTC, datetime

from accretion.contracts import PrincipalRef, PrincipalStatus
from accretion.feedback.experience import ExperienceMaterializer
from accretion.feedback.service import DefaultFeedbackPipeline
from accretion.services.run_manager import RunManager

__all__ = ["build_feedback_pipeline"]


def build_feedback_pipeline(
    manager: RunManager, experiences: ExperienceMaterializer
) -> DefaultFeedbackPipeline:
    """The pipeline as the run manager will hold it: the manager's store, the operator's identity.

    ``created_by`` is the *operator*, not the run's principal, and the two are different on
    purpose. Every record this service writes on nobody's behalf — a §7.9 verification result, a
    §7.11 failure event, the ``OPEN`` revision a new contradiction forces onto an older record —
    is written by the deployment rather than by whichever principal's run happened to trigger it.
    Records a principal did ask for take that principal instead:
    :meth:`~accretion.feedback.service.DefaultFeedbackPipeline.record_final` is handed one and
    authors its experience records with it, because sharing an experience is an act of permission
    and §10.1 requires the provenance to name who granted it.

    ``status`` is ``ACTIVE`` for the same reason
    :func:`~accretion.routing.identity.principal_ref_for_run` gives: this reference is only ever
    the *author* of a record, never the authority for an action, and the question "may this
    principal act" is asked by the gates against a principal resolved from the store.

    The clock is :func:`datetime.now` in UTC, passed as a callable rather than read inside the
    service, which is what lets every test in this milestone pin it: two ingests of one
    verification are byte-identical only if their stamps are.
    """

    return DefaultFeedbackPipeline(
        manager.store,
        experiences,
        lambda: datetime.now(UTC),
        PrincipalRef(
            principal_id=manager.operator_identity,
            display_name=manager.operator_identity,
            status=PrincipalStatus.ACTIVE,
        ),
    )
