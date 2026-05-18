import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
REPO_ROOT   = SERVICE_DIR.parent.parent  # services/<name> → services → repo root

sys.path.insert(0, str(SERVICE_DIR))
sys.path.insert(0, str(REPO_ROOT))
