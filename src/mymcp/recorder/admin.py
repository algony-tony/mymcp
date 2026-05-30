"""Admin endpoints for recorder management.

Exposes:
- POST /admin/overview/bootstrap — schedule a bootstrap run
- GET  /admin/overview/status    — current recorder state and error
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from mymcp.auth import require_admin

router = APIRouter(prefix="/admin/overview", tags=["recorder"])

_supervisor: Any | None = None


def set_supervisor(sup: Any | None) -> None:
    global _supervisor
    _supervisor = sup


def _sup() -> Any:
    if _supervisor is None:
        raise HTTPException(status_code=503, detail="recorder disabled")
    return _supervisor


@router.post("/bootstrap")
async def trigger_bootstrap(_: object = Depends(require_admin)) -> dict[str, Any]:
    sup = _sup()
    sup.request_bootstrap()
    status = sup.status()
    return {"state": status.bootstrap_state.value, "run_id": None}


@router.get("/status")
async def get_status(_: object = Depends(require_admin)) -> dict[str, Any]:
    sup = _sup()
    s = sup.status()
    return {
        "enabled": s.enabled,
        "bootstrap_state": s.bootstrap_state.value,
        "last_bootstrap_ts": s.last_bootstrap_ts,
        "last_merge_ts": s.last_merge_ts,
        "last_merge_age_seconds": s.last_merge_age_seconds,
        "pending_events": s.pending_events,
        "last_error": s.last_error,
        "llm_provider": s.llm_provider,
        "llm_model": s.llm_model,
    }
