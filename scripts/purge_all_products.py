"""Hard-delete every product (and, by cascade, their stock + order history)
via db.delete_product.

This is IRREVERSIBLE and destroys financial records, so it is guarded (review M12):
  - Dry-run by default: prints what WOULD be deleted and stops.
  - To actually delete, pass the literal confirmation token:  --yes-delete-all
  - Takes a timestamped backup of shop.db first.
  - Always prints the resolved DB path so you can see which database you're hitting.

    python -m scripts.purge_all_products              # dry run
    python -m scripts.purge_all_products --yes-delete-all
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

CONFIRM_TOKEN = "--yes-delete-all"


async def main() -> int:
    confirmed = CONFIRM_TOKEN in sys.argv[1:]
    print(f"Database: {db.DB_PATH.resolve()}")

    rows = await db.list_products(active_only=False, limit=10_000)
    if not rows:
        print("No products in DB. Nothing to do.")
        return 0

    print(f"{len(rows)} product(s) would be hard-deleted (with stock + order history):")
    for r in rows:
        print(f"  - #{int(r['id'])} {r['name']!r}")

    if not confirmed:
        print(f"\nDRY RUN — nothing deleted. Re-run with {CONFIRM_TOKEN} to proceed.")
        return 0

    # Backup before destroying anything. The DB runs in WAL mode, so freshly
    # committed rows (recent orders/deposits/wallet credits) can still live in
    # shop.db-wal until a checkpoint. A bare copy of shop.db would silently miss
    # them — restoring it after a bad purge would lose exactly that data. So
    # checkpoint first, then copy ALL shop.db* siblings (-wal/-shm). (review:
    # purge backup ignores WAL)
    if db.DB_PATH.exists():
        ts = int(time.time())
        try:
            import sqlite3
            con = sqlite3.connect(str(db.DB_PATH))
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.close()
        except Exception as e:
            print(f"\nWARNING: WAL checkpoint failed ({e}); backing up files as-is.")
        copied = []
        for p in sorted(db.DB_PATH.parent.glob(db.DB_PATH.name + "*")):
            if p.name.endswith(".bak") or ".bak-" in p.name:
                continue  # don't re-copy prior backups
            dest = p.with_name(f"{p.name}.bak-{ts}")
            shutil.copy2(p, dest)
            copied.append(dest.name)
        print(f"\nBackup written: {', '.join(copied)}")

    print("\nDeleting:")
    for r in rows:
        pid = int(r["id"])
        print(f"  - #{pid} {r['name']!r}", end=" ... ", flush=True)
        print(await db.delete_product(pid))

    remaining = await db.list_products(active_only=False, limit=10_000)
    print(f"\nRemaining products: {len(remaining)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
