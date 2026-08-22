from accretion.models import ApprovalDecision, ProviderName, SessionStatus
from accretion.providers.base import ProviderEvent
from accretion.service import AccretionService


async def test_managed_session_lifecycle(service: AccretionService) -> None:
    workspace = service.settings.workspace_roots[0] / "workspace"
    session = await service.start_session(
        provider=ProviderName.CODEX,
        cwd=str(workspace),
        prompt="Inspect this repository",
    )
    assert session.managed is True
    assert session.status == SessionStatus.RUNNING
    assert session.provider_session_id

    await service.send_message(session.id, "Focus on tests")
    interrupted = await service.interrupt(session.id)
    assert interrupted.status == SessionStatus.INTERRUPTED


async def test_approval_round_trip(service: AccretionService) -> None:
    workspace = service.settings.workspace_roots[0] / "workspace"
    session = await service.start_session(
        provider=ProviderName.CLAUDE,
        cwd=str(workspace),
        prompt="Run the tests",
    )
    await service.handle_provider_event(
        ProviderEvent(
            session.id,
            "approval",
            {
                "provider_request_id": "request-1",
                "approval_kind": "tool:Bash",
                "command": "pytest",
            },
        )
    )
    detail = await service.get_session(session.id)
    assert detail.status == SessionStatus.WAITING_APPROVAL
    assert len(detail.approvals) == 1

    resolved = await service.decide_approval(detail.approvals[0].id, ApprovalDecision.APPROVE)
    assert resolved.status == "approved"
