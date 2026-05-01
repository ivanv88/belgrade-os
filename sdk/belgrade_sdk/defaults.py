from __future__ import annotations
import os

# --- Service Discovery Defaults ---
# These are the default URLs used if the corresponding BEG_OS_* environment 
# variables are not set. The Platform Controller should ideally inject these.

BRIDGE_URL = os.getenv("BEG_OS_BRIDGE_URL", "http://localhost:8081")
REDIS_URL = os.getenv("BEG_OS_REDIS_URL", "redis://localhost:6379")
DB_URL = os.getenv("BEG_OS_DB_URL")  # Usually provided by platform
CALLBACK_URL = os.getenv("BEG_OS_CALLBACK_URL", "http://localhost:9000")

# --- Redis Transport Constants ---
# Centralized stream names and group IDs to ensure consistency across services.

STREAM_TASKS_INBOUND = "tasks:inbound"
STREAM_TASKS_TOOL_CALLS = "tasks:tool_calls"
STREAM_TASKS_TOOL_RESULTS = "tasks:tool_results"
STREAM_NOTIFICATIONS = "tasks:notifications"

CONSUMER_GROUP_NOTIFICATIONS = "notification-workers"

# --- Notification Defaults ---
DEFAULT_NOTIFICATION_DRIVER = "ntfy"
