# 🇷🇸 Belgrade AI OS: Specification & Roadmap

**Version:** 1.0.0  
**Host:** Raspberry Pi 4 (4GB RAM)  
**Orchestrator:** MBP M2 (32GB RAM)  
**Security:** Cloudflare Zero Trust (MFA)

---

## 1. System Vision
A "Modular Monolith" designed for personal orchestration. The **Core** acts as a generic engine (the OS), while **Apps** (Nutrition, Agents, Health) act as pluggable modules. This allows for local-first intelligence with high portability.

## 2. Technical Stack
| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **Hardware** | Raspberry Pi 4 | 24/7 Production Host |
| **Backend** | FastAPI + SQLModel | Generic Core + Plugin API |
| **Frontend** | React + Vite + Shadcn/UI | PWA Management Interface |
| **Database** | PostgreSQL (Docker) | Persistent Memory & Logs |
| **Security** | Cloudflare Tunnel | Identity-based Access (beg-os.fyi) |

---

## 3. Core Architecture (The Engine)

### A. The Dynamic Loader
The Core scans the `/apps` directory at runtime. If a folder contains a valid `api/routes.py`, it is automatically mounted. This ensures the Core remains generic and apps remain decoupled.

### B. Global Context (`core/context.py`)
Centralized source of truth for user-specific metrics to avoid hardcoding:
- **User:** Laurent (38 yo, 190cm, 104kg)
- **Goal:** 20% Calorie Deficit
- **Location:** Belgrade, Serbia

### C. The Plugin Contract
Each app in `/apps` follows this structure:
- `api/routes.py`: Endpoints for the UI.
- `models.py`: SQLModel database schemas.
- `main.py`: Background async worker logic.

---

## 4. Implementation Roadmap

### ✅ Phase 1: Infrastructure (Complete)
- [x] Cloudflare Tunnel & Zero Trust Auth (Email OTP).
- [x] GitHub Monorepo sync between MBP M2 and Pi.
- [x] Professional modular folder architecture.
- [x] PostgreSQL Docker container running on Pi.

### 🕒 Phase 2: Persistence & Context (Current)
- [ ] **Global Context:** Implement `core/context.py` with metabolic calculation logic.
- [ ] **DB Connection:** Establish the SQLAlchemy/SQLModel bridge to Postgres.
- [ ] **Migrations:** Auto-generate tables on system startup.

### ⬜ Phase 3: Module Alpha (Nutrition)
- [ ] Define `NutritionLog` schema (Calories, Protein, Timestamp).
- [ ] Build logic to calculate "Remaining Calories" based on 20% deficit.
- [ ] Integrate `ntfy.sh` for push alerts when goals are met/missed.

### ⬜ Phase 4: Interface & Agents
- [ ] **React Shell:** Build the dashboard on M2 using Shadcn.
- [ ] **Coding Agents:** Orchestrate parallel local agents for repo management.
- [ ] **Obsidian Sync:** Agent to scan markdown notes for task extraction.

---

## 5. Security & Data Policy
- **Access Control:** Restricted to specific emails via Cloudflare Zero Trust.
- **Persistence:** All data stored in `/data/postgres` (Git-ignored).
- **Secrets:** Managed via `.env` file; never committed to version control.
