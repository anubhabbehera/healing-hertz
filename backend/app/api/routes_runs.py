from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repo

from .deps import get_session

router = APIRouter(prefix="/api", tags=["runs"])


@router.get("/runs")
async def list_runs(
    limit: int = 50, offset: int = 0, session: AsyncSession = Depends(get_session)
) -> list[dict]:
    runs = await repo.list_runs(session, limit=limit, offset=offset)
    return [repo._run_summary(r) for r in runs]


@router.get("/runs/latest")
async def latest_run(session: AsyncSession = Depends(get_session)) -> dict:
    run = await repo.latest_completed_run(session)
    if run is None:
        raise HTTPException(status_code=404, detail="No completed runs yet")
    detail = repo.run_detail_dict(run)
    previous = await repo.previous_completed_run(session, run.started_at)
    detail["previous_health_score"] = previous.health_score if previous else None
    return detail


@router.get("/runs/compare")
async def compare_runs(a: str, b: str, session: AsyncSession = Depends(get_session)) -> dict:
    try:
        return await repo.compare_runs(session, a, b)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found")


@router.get("/runs/{run_id}")
async def run_detail(run_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    run = await repo.get_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return repo.run_detail_dict(run)


@router.get("/trends")
async def trends(
    metric: str, subject_id: str | None = None, session: AsyncSession = Depends(get_session)
) -> dict:
    points = await repo.get_trends(session, metric, subject_id)
    subjects = await repo.list_metric_subjects(session, metric)
    return {"metric": metric, "subjects": subjects, "points": points}
