#!/usr/bin/env python3
"""Consistent, self-rotating backups for the telegram-shop VPS.

Stdlib-only (no venv needed) so it keeps working even mid-deploy. Run from cron
every 30 min. Takes a crash-consistent snapshot of shop.db via SQLite's online
backup API (safe while the bot is writing, WAL or not), verifies it with
PRAGMA integrity_check, gzips it, and snapshots .env + logo/emoji id-maps when
they change. Old snapshots are thinned automatically.

  Recent  : every snapshot kept for KEEP_ALL_DAYS days (fine-grained).
  Older   : one per UTC day kept up to KEEP_DAILY_DAYS days.
  Beyond  : deleted.

Override paths/retention via env vars (TS_APP_DIR, TS_BACKUP_DIR, etc.).
"""
import os
import sys
import gzip
import glob
import time
import shutil
import hashlib
import sqlite3
import tarfile
import datetime

APP_DIR = os.environ.get("TS_APP_DIR", "/opt/telegram-shop")
DB_PATH = os.environ.get("TS_DB_PATH", os.path.join(APP_DIR, "shop.db"))
BACKUP_DIR = os.environ.get("TS_BACKUP_DIR", "/var/backups/telegram-shop")
KEEP_ALL_DAYS = int(os.environ.get("TS_KEEP_ALL_DAYS", "3"))
KEEP_DAILY_DAYS = int(os.environ.get("TS_KEEP_DAILY_DAYS", "30"))

DB_PREFIX = "shop.db."
DB_SUFFIX = ".gz"
CFG_PREFIX = "config."
CFG_SUFFIX = ".tar.gz"
TS_FMT = "%Y%m%d-%H%M%S"


def log(msg):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{stamp}] {msg}", flush=True)


def _ts_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime(TS_FMT)


def backup_db(ts):
    """Crash-consistent snapshot of shop.db -> shop.db.<ts>.gz. Returns path or None."""
    if not os.path.exists(DB_PATH):
        log(f"WARNING: db not found at {DB_PATH} (deploy in progress?) — skipping db backup")
        return None
    tmp = os.path.join(BACKUP_DIR, f"{DB_PREFIX}{ts}.tmp")
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)            # online backup API: consistent, no lock-out
            row = dst.execute("PRAGMA integrity_check").fetchone()
        finally:
            dst.close()
    finally:
        src.close()
    if not row or row[0] != "ok":
        bad = os.path.join(BACKUP_DIR, f"{DB_PREFIX}{ts}.CORRUPT")
        os.replace(tmp, bad)
        log(f"ERROR: integrity_check failed ({row}); saved suspect copy {bad}")
        raise SystemExit(2)
    final = os.path.join(BACKUP_DIR, f"{DB_PREFIX}{ts}{DB_SUFFIX}")
    with open(tmp, "rb") as fi, gzip.open(final, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo)
    os.remove(tmp)
    os.chmod(final, 0o600)
    # Convenience pointer to the newest good db backup.
    latest = os.path.join(BACKUP_DIR, "shop.db.latest.gz")
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(final, latest)
    except OSError:
        pass
    log(f"db backup OK -> {final} ({os.path.getsize(final)} bytes)")
    return final


def _config_members():
    members = []
    env = os.path.join(APP_DIR, ".env")
    if os.path.exists(env):
        members.append((env, ".env"))
    for p in sorted(glob.glob(os.path.join(APP_DIR, "keyboards", "*_ids.json"))):
        members.append((p, os.path.join("keyboards", os.path.basename(p))))
    logos = os.path.join(APP_DIR, "assets", "brand-logos")
    if os.path.isdir(logos):
        for root, _, files in os.walk(logos):
            for f in files:
                full = os.path.join(root, f)
                members.append((full, os.path.relpath(full, APP_DIR)))
    return members


def _config_hash(members):
    h = hashlib.sha256()
    for full, arc in sorted(members, key=lambda m: m[1]):
        try:
            with open(full, "rb") as fh:
                h.update(arc.encode())
                h.update(b"\0")
                h.update(fh.read())
        except OSError:
            continue
    return h.hexdigest()


def backup_config(ts):
    """Snapshot .env + id-maps + brand logos, but only when they changed."""
    members = _config_members()
    if not members:
        return None
    digest = _config_hash(members)
    marker = os.path.join(BACKUP_DIR, ".config.lasthash")
    prev = ""
    if os.path.exists(marker):
        with open(marker) as fh:
            prev = fh.read().strip()
    if digest == prev:
        return None  # nothing changed since last run — skip the churn
    final = os.path.join(BACKUP_DIR, f"{CFG_PREFIX}{ts}{CFG_SUFFIX}")
    with tarfile.open(final, "w:gz") as tar:
        for full, arc in members:
            tar.add(full, arcname=arc)
    os.chmod(final, 0o600)
    with open(marker, "w") as fh:
        fh.write(digest)
    os.chmod(marker, 0o600)
    log(f"config changed -> {final} ({os.path.getsize(final)} bytes)")
    return final


def _parse_ts(name, prefix, suffix):
    if not (name.startswith(prefix) and name.endswith(suffix)):
        return None
    core = name[len(prefix):-len(suffix)]
    try:
        return datetime.datetime.strptime(core, TS_FMT).replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def prune(prefix, suffix):
    now = datetime.datetime.now(datetime.timezone.utc)
    items = []
    for name in os.listdir(BACKUP_DIR):
        when = _parse_ts(name, prefix, suffix)
        if when:
            items.append((when, os.path.join(BACKUP_DIR, name)))
    items.sort(reverse=True)
    kept_days = set()
    removed = 0
    for when, path in items:
        age_days = (now - when).total_seconds() / 86400.0
        if age_days <= KEEP_ALL_DAYS:
            continue  # keep every snapshot in the recent window
        if age_days > KEEP_DAILY_DAYS:
            os.remove(path); removed += 1; continue
        day = when.strftime("%Y%m%d")
        if day in kept_days:
            os.remove(path); removed += 1  # already kept one for this day
        else:
            kept_days.add(day)
    if removed:
        log(f"pruned {removed} old {prefix}* file(s)")


def main():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    os.chmod(BACKUP_DIR, 0o700)
    ts = _ts_now()
    try:
        backup_db(ts)
        backup_config(ts)
        prune(DB_PREFIX, DB_SUFFIX)
        prune(CFG_PREFIX, CFG_SUFFIX)
    except SystemExit:
        raise
    except Exception as e:  # never let a transient error kill the cron silently
        log(f"ERROR: backup failed: {e!r}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
