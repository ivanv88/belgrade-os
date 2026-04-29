import sys
import os
from pathlib import Path

# Add SDK to path so we can import it
sys.path.append(str(Path(__file__).parent.parent.parent / "sdk"))

from belgrade_sdk.app import BelgradeApp
from belgrade_sdk.context import AppContext

app = BelgradeApp(app_id="shopping")

@app.tool(
    name="add_item",
    description="Add an item to the household shopping list"
)
async def add_item(ctx: AppContext, item_name: str, quantity: str = "1"):
    # In a real app, we'd use ctx.db to save to Postgres
    # For now, let's just simulate and notify
    
    msg = f"🛒 Added {quantity}x {item_name} to the list (User: {ctx.user_id})"
    await ctx.notify(msg)
    
    return {
        "status": "success",
        "item": item_name,
        "quantity": quantity,
        "tenant": ctx.tenant_id
    }

if __name__ == "__main__":
    # To run this, we'd set env vars:
    # BEG_OS_BRIDGE_URL=http://localhost:8081
    # BEG_OS_CALLBACK_URL=http://localhost:9001
    app.run(port=9001)
