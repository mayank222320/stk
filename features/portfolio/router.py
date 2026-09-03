from fastapi import APIRouter
from features.portfolio.service import get_positions, delete_position, clear_all_positions

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])

@router.get("/positions")
async def positions():
    return await get_positions()

@router.delete("/positions/all")
async def clear_all():
    count = await clear_all_positions()
    return {"status": "success", "deleted": count}

@router.delete("/positions/{position_id}")
async def delete_pos(position_id: str):
    success = await delete_position(position_id)
    return {"status": "success" if success else "failed"}
