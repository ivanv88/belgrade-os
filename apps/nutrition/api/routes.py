from fastapi import APIRouter

router = APIRouter()

@router.get("/")
async def get_nutrition_root():
    return {"status": "Nutrition module active", "deficit_goal": "20%"}
