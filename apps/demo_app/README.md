# System Integration Demo App

This app serves as the primary verification tool for a fresh Belgrade OS installation. It exercises the full 6-service stack in a single end-to-end flow.

## 🏗️ What it Tests

1.  **Gateway (UI):** Securely serves the web dashboard from `static/web/`.
2.  **Gateway (API):** Processes the tool execution request.
3.  **Bridge (Rust):** Correctly routes the tool call to the Python process.
4.  **Inference (Gemini):** Uses the Gemini driver to simulate AI reasoning.
5.  **Vault Service:** Performs an indirect, atomic write to the Obsidian vault via Redis.
6.  **Notification Service:** Dispatches a "Success" alert to your configured driver (e.g., ntfy).
7.  **Pub/Sub:** Emits a `demo.test_completed` event and verifies the app catches it.

## 🚀 How to Run the Demo

### 1. Ensure Services are Running
Ensure your infrastructure is up (`make dev`) and the core services are running.

### 2. Seed Permissions
You must authorize yourself to view the demo UI:
```bash
python3 scripts/seed_permissions.py
```

### 3. Open the Dashboard
Visit the following URL in your browser (replacing with your domain if remote):
`https://beg-os.fyi/ui/demo_app/web/`

### 4. Execute the Test
1. Click the **"Run Integration Test"** button.
2. Observe the service list on the screen. Each item will turn **Green** as that service successfully processes its part of the chain.
3. Check your **Obsidian Vault** folder (default `/tmp/belgrade-vault`). You should see a new file: `Tests/demo-xxx.md`.
4. Check your **Notification Client** (phone or ntfy topic). You should receive a "System Test Complete" alert.

## 📁 File Structure
- `main.py`: The app logic using the Belgrade SDK.
- `manifest.json`: Defines the UI bundles and metadata.
- `static/web/index.html`: The interactive test dashboard.
