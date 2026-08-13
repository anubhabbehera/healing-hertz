from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo

from .deps import get_session

router = APIRouter(prefix="/api/dismissals", tags=["dismissals"])


class DismissalCreate(BaseModel):
    rule_id: str
    # Omit to dismiss the rule for every subject (site-wide).
    subject_id: str | None = None
    subject_name: str | None = None
    title: str | None = None
    reason: str | None = Field(None, max_length=500)


@router.get("")
async def list_dismissals(session: AsyncSession = Depends(get_session)) -> list[dict]:
    return [repo.dismissal_dict(d) for d in await repo.list_dismissals(session)]


@router.post("", status_code=201)
async def create_dismissal(
    body: DismissalCreate, session: AsyncSession = Depends(get_session)
) -> dict:
    """Acknowledge a finding as won't-fix.

    Applies to past and future scans: stored runs are re-scored immediately so
    the health score and its trend reflect the operator's current judgement.
    """
    dismissal = await repo.add_dismissal(
        session,
        rule_id=body.rule_id,
        subject_id=body.subject_id,
        subject_name=body.subject_name,
        title=body.title,
        reason=body.reason,
    )
    return repo.dismissal_dict(dismissal)


@router.delete("/{dismissal_id}", status_code=204)
async def delete_dismissal(
    dismissal_id: int, session: AsyncSession = Depends(get_session)
) -> None:
    if not await repo.remove_dismissal(session, dismissal_id):
        raise HTTPException(status_code=404, detail="Dismissal not found")
