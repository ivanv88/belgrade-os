#!/bin/bash

echo "🇷🇸 Belgrade AI OS - Environment Setup"

# 1. System dependencies for Python and Postgres
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
pip install fastapi uvicorn requests psutil watchdog python-dotenv

# 4. Create directory structure if missing
mkdir -p apps core shared data logs

echo "🚀 Setup complete! Use 'source venv/bin/activate' to start."
