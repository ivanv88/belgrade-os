#!/bin/bash

echo "🇷🇸 Belgrade AI OS - Environment Setup"

# 1. System dependencies for Python 3.11+ and Postgres
# Requires Python 3.11+ (fastmcp and asyncpg need >= 3.10)
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip libpq-dev python3-dev build-essential

# 2. Create and activate Virtual Environment
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Virtual environment created."
fi

source venv/bin/activate

# 3. Upgrade pip and install core requirements
pip install --upgrade pip
pip install fastapi uvicorn requests psutil watchdog apscheduler python-dotenv \
    sqlmodel pydantic-settings psycopg2-binary asyncpg \
    fastmcp pyyaml httpx \
    pytest pytest-asyncio pytest-mock mypy

# 4. Create directory structure if missing
mkdir -p apps core shared data logs

echo "🚀 Setup complete! Use 'source venv/bin/activate' to start."
