from .base import NotificationDriver
from .ntfy import NtfyDriver


def get_driver(cfg) -> NotificationDriver:
    """Factory — maps NOTIFICATION_DRIVER config value to a driver instance.

    To add a new driver: create a subclass of NotificationDriver and add
    a branch here. No other files need to change.
    """
    if cfg.notification_driver == "ntfy":
        return NtfyDriver(base_url=cfg.ntfy_base_url, topic=cfg.ntfy_topic)
    raise ValueError(f"Unknown notification driver: {cfg.notification_driver!r}")
