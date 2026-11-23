"""Backend alias for the project-wide settings in ``config/settings.py``."""
from config.settings import PROJECT_ROOT, Settings, get_settings

__all__ = ["PROJECT_ROOT", "Settings", "get_settings"]
