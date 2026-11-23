"""Backend alias for the connection manager in ``database/connection.py``."""
from database.connection import (
    DatabaseConnectionError,
    DatabaseManager,
    database_manager,
    get_database_manager,
)

__all__ = [
    "DatabaseConnectionError",
    "DatabaseManager",
    "database_manager",
    "get_database_manager",
]
