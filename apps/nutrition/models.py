from sqlmodel import SQLModel, Field
from datetime import datetime
from typing import Optional

class NutritionLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    item_name: str
    calories: int
    protein: Optional[float] = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)
    is_deficit_day: bool = True # Flag to track if you stayed under your limit
