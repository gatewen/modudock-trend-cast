"""One durable, process-safe 3000 HTTP-attempt budget for the evolution campaign.

The ledger is separate from experiment databases, so copying/reopening a DB
does not reset it. Every real JevClient transport, including retries and the
existing CLI tools, consumes a committed permit immediately before open().
"""
from pathlib import Path
import sqlite3

from .http_client import ClientError

LIMIT = 3000
LEDGER = Path(__file__).resolve().parents[1] / 'data' / 'evolve-2026-09-27-budget.sqlite3'


def consume(path=LEDGER):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(path, timeout=15) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS budget (
                campaign TEXT PRIMARY KEY, used INTEGER NOT NULL CHECK(used>=0))''')
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR IGNORE INTO budget VALUES ('evolve/2026-09-27',0)")
            used = db.execute("SELECT used FROM budget WHERE campaign='evolve/2026-09-27'").fetchone()[0]
            if used >= LIMIT:
                raise ClientError('evolution_budget_exhausted')
            db.execute("UPDATE budget SET used=used+1 WHERE campaign='evolve/2026-09-27'")
    except ClientError:
        raise
    except Exception:
        raise ClientError('evolution_budget_unavailable') from None
