"""Screenshots of the form/confirmation page, stored under server-generated names in ``storage/screenshots/``.

Screenshots can contain personal data (a filled form): they stay on disk, are served only through the API for their own application,
are never logged, and are never overwritten. Ids are opaque (no paths).
"""
import logging
import re
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)
_ID = re.compile(r"^[0-9a-f]{12}_[0-9a-f]{16}\.png$")


class ScreenshotManager:
    def __init__(self, directory: Path):
        self.dir = Path(directory)

    def save(self, png: bytes, application_id: str) -> str:
        self.dir.mkdir(parents=True, exist_ok=True)
        sid = f"{application_id[:12]}_{uuid.uuid4().hex[:16]}.png"
        with open(self.dir / sid, "xb") as fh:  # never overwrite
            fh.write(png)
        logger.info("Screenshot saved: id=%s size=%d", sid, len(png))
        return sid

    def capture(self, page, application_id: str, full_page: bool = True) -> str | None:
        """Best effort: a failed screenshot never fails the application."""
        try:
            return self.save(page.screenshot(full_page=full_page, type="png"), application_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Screenshot failed: %s", type(exc).__name__)
            return None

    def read(self, sid: str) -> bytes | None:
        if not _ID.match(sid or ""):
            return None
        base = self.dir.resolve()
        path = (base / sid).resolve()
        return path.read_bytes() if path.is_relative_to(base) and path.is_file() else None

    def delete(self, ids: list[str]) -> int:
        n = 0
        for sid in ids:
            if _ID.match(sid or ""):
                try:
                    (self.dir / sid).unlink()
                    n += 1
                except OSError:
                    pass
        return n
