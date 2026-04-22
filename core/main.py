from fastapi import FastAPI
from core.loader import load_plugins

app = FastAPI(title="Belgrade AI OS")

@app.get("/")
async def root():
    return {"message": "Zdravo, Laurent!", "system": "Belgrade OS Core"}

# This is where the magic happens
load_plugins(app)
