import importlib
import os
from fastapi import FastAPI

def load_plugins(app: FastAPI):
    """Dynamically registers routes from each app in the /apps folder."""
    apps_dir = "apps"
    for item in os.listdir(apps_dir):
        # Check if it's a valid app directory
        if os.path.isdir(os.path.join(apps_dir, item)) and item != "__pycache__":
            try:
                # Try to import the app's router
                router_module = importlib.import_module(f"apps.{item}.api.routes")
                app.include_router(router_module.router, prefix=f"/{item}", tags=[item.capitalize()])
                print(f"✅ Loaded App: {item}")
            except ImportError:
                print(f"ℹ️  App '{item}' has no router defined.")
