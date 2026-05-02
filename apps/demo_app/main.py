from __future__ import annotations
import logging
from belgrade_sdk.app import BelgradeApp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BelgradeApp("demo_app")

@app.tool("run_full_test", description="Executes an end-to-end system test")
async def run_full_test(ctx, user_prompt: str):
    logger.info("Starting Full System Test for prompt: %s", user_prompt)
    
    # 1. Test Inference (using Gemini driver)
    # We simulate an inference call. In a real app, this would use a standard SDK method.
    # For now, we'll demonstrate the intent.
    summary = f"Gemini processed: {user_prompt}"
    
    # 2. Test Vault Service (Indirect Write)
    vault_path = f"Tests/demo-{ctx.trace_id}.md"
    await ctx.vault.write(vault_path, f"# System Test Result\n\n- **Prompt:** {user_prompt}\n- **Result:** {summary}")
    
    # 3. Test Notification Service
    await ctx.notify(
        title="System Test Complete",
        body=f"Summary saved to vault: {vault_path}",
        priority="HIGH",
        tags=["demo", "success"]
    )
    
    # 4. Test Event Bus (Bridge)
    await ctx.emit("demo.test_completed", {"path": vault_path, "status": "success"})
    
    return {"status": "success", "vault_path": vault_path}

@app.on_event("demo.test_completed")
async def handle_completion(ctx, payload):
    logger.info("Demo App caught its own completion event! %s", payload)

if __name__ == "__main__":
    app.run(port=9005)
