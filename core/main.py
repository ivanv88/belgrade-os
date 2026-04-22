from fastapi import FastAPI
from core.loader import load_plugins
from shared.database import init_db

app = FastAPI(title="Belgrade AI OS")

@app.on_event("startup")
def on_startup():
    # When the OS boots, create any new tables from your apps
    init_db()

@app.get("/")
async def root():
    return {"message": "Zdravo, Laurent!", "db_status": "Connected"}

load_plugins(app)
