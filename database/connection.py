"""Reusable MongoDB connection manager (single shared PyMongo client)."""
import logging
import time
from typing import Callable

from pymongo import MongoClient
from pymongo.database import Database
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)


class DatabaseConnectionError(Exception):
    """Raised when MongoDB is unreachable or not connected.

    Messages are intentionally generic: they must never contain credentials.
    """


class DatabaseManager:
    """Owns one MongoClient for the whole process."""

    RECONNECT_EVERY_S = 5.0

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._client: MongoClient | None = None
        self._database_name: str | None = None
        self._uri: str | None = None            # remembered so a database that starts later is picked up (never logged)
        self._timeout_ms = 3000
        self._clock, self._last_attempt = clock, None
        self.on_connect: Callable[[Database], None] | None = None   # e.g. ensure_indexes

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    @property
    def database_name(self) -> str | None:
        return self._database_name

    def connect(self, uri: str, database: str, timeout_ms: int = 3000) -> None:
        """Create the client and verify connectivity with ``ping``."""
        if self._client is not None:
            return
        self._uri, self._database_name, self._timeout_ms = uri, database, timeout_ms
        self._last_attempt = self._clock()
        client: MongoClient | None = None
        try:
            client = MongoClient(uri, serverSelectionTimeoutMS=timeout_ms, tz_aware=True)
            client.admin.command("ping")
        except PyMongoError as exc:
            if client is not None:
                client.close()
            logger.error("MongoDB connection failed: %s", type(exc).__name__)
            raise DatabaseConnectionError("Unable to connect to MongoDB") from None
        self._client = client
        logger.info("Connected to MongoDB (database=%s)", database)
        if self.on_connect is not None:
            try:
                self.on_connect(client[database])
            except Exception as exc:  # noqa: BLE001
                logger.error("MongoDB post-connect hook failed: %s", type(exc).__name__)

    def ping(self) -> bool:
        """Return True if the server answers a ping; never raises."""
        if self._client is None:
            return False
        try:
            self._client.admin.command("ping")
            return True
        except PyMongoError as exc:
            logger.warning("MongoDB ping failed: %s", type(exc).__name__)
            return False

    def get_database(self) -> Database:
        if self._client is None and self._uri and self._database_name and (self._last_attempt is None or self._clock() - self._last_attempt >= self.RECONNECT_EVERY_S):
            try:                                   # the database may have come up after the API: try again (at most every few seconds)
                self.connect(self._uri, self._database_name, self._timeout_ms)
            except DatabaseConnectionError:
                pass
        if self._client is None or self._database_name is None:
            raise DatabaseConnectionError("MongoDB is not connected")
        return self._client[self._database_name]

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            logger.info("MongoDB connection closed")
        self._client = None
        self._database_name = None
        self._uri = None


# Process-wide instance, initialised in the FastAPI lifespan.
database_manager = DatabaseManager()


def get_database_manager() -> DatabaseManager:
    """FastAPI dependency (override in tests)."""
    return database_manager
