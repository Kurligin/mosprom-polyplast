"""REST для Mini App. Контракт — miniapp/API.md, вход — app/api/auth.py."""

from fastapi import APIRouter

from app.api import employees, kb, misc, tickets

router = APIRouter(prefix="/api")


@router.get("/ping")
async def ping() -> dict:
    return {"pong": True}


router.include_router(misc.router)
router.include_router(employees.router)
router.include_router(tickets.router)
router.include_router(kb.router)
