from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {
        "message": "Zdravo, Laurent!",
        "status": "Running on 4GB Pi",
        "tunnel": "Active"
    }
