from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

from app.config import get_settings
from app.integrations.ui_icons import IconCatalogue

router = APIRouter(prefix="/api", tags=["icons"])

# A day in the browser cache: product art for a given model never changes, and
# the backend already holds the bytes on disk.
CACHE_CONTROL = "public, max-age=86400"


@lru_cache
def _catalogue() -> IconCatalogue:
    return IconCatalogue(Path(get_settings().icon_cache_dir))


@router.get("/device-icons/{model}", response_class=Response)
async def device_icon(model: str) -> Response:
    """Product icon for a hardware model, or 404 for the UI to draw its own."""
    if not get_settings().device_icons:
        raise HTTPException(status_code=404, detail="Device icons are disabled")
    png = await _catalogue().png(model)
    if png is None:
        raise HTTPException(status_code=404, detail=f"No icon for model '{model}'")
    return Response(content=png, media_type="image/png", headers={"Cache-Control": CACHE_CONTROL})
