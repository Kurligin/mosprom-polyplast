"""REST для Mini App. Эндпоинты — в следующем раунде."""

from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}
