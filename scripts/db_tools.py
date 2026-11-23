"""Database validation, backup and restore.

  uv run python scripts/db_tools.py validate
  uv run python scripts/db_tools.py backup  --to backups/2026-11-13 [--encrypt]
  uv run python scripts/db_tools.py verify  --from backups/2026-11-13
  uv run python scripts/db_tools.py restore --from backups/2026-11-13 --target-db ai_jobpilot_restored [--encrypt-key-from-env]

Restore NEVER defaults to the live database: you must name a target. Restoring over the live database needs --allow-non-empty and is your explicit decision.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import MongoClient  # noqa: E402

from config.settings import get_settings  # noqa: E402
from database.maintenance import BackupError, backup, restore, validate, verify_backup  # noqa: E402


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate")
    b = sub.add_parser("backup")
    b.add_argument("--to", required=True)
    b.add_argument("--encrypt", action="store_true", help="encrypt with a key derived from SECRET_KEY")
    v = sub.add_parser("verify")
    v.add_argument("--from", dest="src", required=True)
    r = sub.add_parser("restore")
    r.add_argument("--from", dest="src", required=True)
    r.add_argument("--target-db", required=True)
    r.add_argument("--allow-non-empty", action="store_true")
    a = p.parse_args(argv)
    s = get_settings()
    client = MongoClient(s.mongodb_uri, serverSelectionTimeoutMS=3000, tz_aware=True)
    try:
        if a.cmd == "validate":
            rep = validate(client[s.mongodb_database])
            print(json.dumps(rep.as_dict(), indent=2, default=str))
            return 0 if rep.ok else 1
        if a.cmd == "backup":
            m = backup(client[s.mongodb_database], Path(a.to), secret_key=s.secret_key, encrypt=a.encrypt)
            print(f"Backup written to {a.to}: " + ", ".join(f"{k}={v['documents']}" for k, v in m["collections"].items()))
            return 0
        if a.cmd == "verify":
            m = verify_backup(Path(a.src), secret_key=s.secret_key)
            print(f"OK: {len(m['collections'])} collection(s), created {m['created_at']}, encrypted={m['encrypted']}")
            return 0
        if a.target_db == s.mongodb_database and not a.allow_non_empty:
            print("Refusing to restore into the live database without --allow-non-empty.")
            return 2
        out = restore(client[a.target_db], Path(a.src), secret_key=s.secret_key, allow_non_empty=a.allow_non_empty)
        print(f"Restored into {out['database']}: " + ", ".join(f"{k}={v}" for k, v in out["restored"].items()))
        return 0
    except BackupError as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
