#!/bin/bash

# 🇷🇸 Belgrade OS - Universal Setup Script
# Works on macOS (brew) and Linux (apt)

set -e

echo "🇷🇸 Belgrade OS Setup starting..."

# --- 1. OS Detection ---
OS="$(uname)"
case "${OS}" in
    Darwin*)    PACKAGE_MANAGER="brew";;
    Linux*)     PACKAGE_MANAGER="apt";;
    *)          echo "❌ Unsupported OS: ${OS}"; exit 1;;
esac

echo "Detected OS: ${OS} (Using ${PACKAGE_MANAGER})"

# --- 2. System Dependency Check ---
check_binary() {
    if ! command -v "$1" &> /dev/null; then
        echo "⚠️  $1 is not installed."
        return 1
    fi
    echo "✅ $1 is installed."
    return 0
}

install_system_deps() {
    if [ "${PACKAGE_MANAGER}" == "brew" ]; then
        brew install protobuf go rust python3 docker docker-compose
    else
        sudo apt-get update
        sudo apt-get install -y protobuf-compiler golang-go rustc cargo python3 python3-venv python3-pip libpq-dev build-essential docker.io docker-compose
    fi
}

echo "--- Checking System Prerequisites ---"
MISSING_DEPS=0
check_binary protoc || MISSING_DEPS=1
check_binary go || MISSING_DEPS=1
check_binary rustc || MISSING_DEPS=1
check_binary python3 || MISSING_DEPS=1
check_binary docker || MISSING_DEPS=1

if [ $MISSING_DEPS -eq 1 ]; then
    read -p "Some system dependencies are missing. Install them now? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        install_system_deps
    else
        echo "❌ Cannot proceed without system dependencies."
        exit 1
    fi
fi

# --- 3. Python Virtual Environment ---
echo "--- Setting up Python Virtual Environment ---"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ Virtual environment created."
fi

source venv/bin/activate
pip install --upgrade pip

# --- 4. Belgrade OS Core Setup ---
echo "--- Installing Project Dependencies ---"
make deps

echo "--- Generating Protobuf Code ---"
make proto

echo "--- Building Binaries ---"
make build

# --- 5. Environment & Database ---
echo "--- Finalizing Setup ---"
if [ ! -f ".env" ]; then
    echo "Creating template .env file..."
    cat <<EOF > .env
# Belgrade OS Configuration
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/postgres
DB_PASSWORD=postgres
BEG_OS_REDIS_URL=redis://localhost:6379
BEG_OS_BRIDGE_URL=http://localhost:8081
BEG_OS_VAULT_PATH=$(pwd)/vault

# AI Provider Keys
ANTHROPIC_API_KEY=your_key_here
GOOGLE_API_KEY=your_key_here

# Cloudflare Zero Trust
CF_TEAM_DOMAIN=your-team
CF_AUDIENCE=your-aud-tag
EOF
    echo "⚠️  Please update .env with your actual API keys!"
fi

mkdir -p vault logs apps data/postgres data/redis

echo ""
echo "🇷🇸 Belgrade OS is ready!"
echo "---------------------------------------------------------"
echo "1. Activate venv:   source venv/bin/activate"
echo "2. Start infra:     make dev"
echo "3. Seed perms:      python3 scripts/seed_permissions.py"
echo "4. Start OS:        cd platform_controller && python3 main.py"
echo "---------------------------------------------------------"
