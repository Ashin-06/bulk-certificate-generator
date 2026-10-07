import logging
import threading

from app.certificates import render_certificate
from app.database import Database

logger = logging.getLogger(__name__)


def process_one(database: Database, renderer=render_certificate) -> bool:
    """Claim one item; commit its output and status together after rendering."""
    with database.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("""
            SELECT r.*, j.title, j.issuer, j.issued_on FROM recipients r
            JOIN jobs j ON j.id = r.job_id
            WHERE r.status = 'pending'
            ORDER BY j.created_at, r.position LIMIT 1
        """).fetchone()
        if row is None:
            return False
        db.execute("UPDATE recipients SET status = 'processing' WHERE id = ?", (row["id"],))

    try:
        svg = renderer(row["name"], row["title"], row["issuer"], row["issued_on"], row["id"])
        if not isinstance(svg, str) or not svg.startswith("<svg"):
            raise ValueError("Renderer did not return a certificate")
    except Exception:
        logger.exception("Certificate generation failed: %s", row["id"])
        with database.connect() as db:
            db.execute("""UPDATE recipients SET status = 'failed', error_code = 'generation_error',
                          error = 'Certificate generation failed' WHERE id = ?""", (row["id"],))
    else:
        with database.connect() as db:
            db.execute("UPDATE recipients SET status = 'succeeded', svg = ? WHERE id = ?", (svg, row["id"]))
    return True


def run_worker(database: Database, stop: threading.Event):
    # Only one application process may own this database (see README).
    with database.connect() as db:
        db.execute("UPDATE recipients SET status = 'pending' WHERE status = 'processing'")
    while not stop.is_set():
        try:
            if not process_one(database):
                stop.wait(0.2)
        except Exception:
            logger.exception("Worker database operation failed; retrying")
            # A write failure after a claim leaves an item in processing.
            try:
                with database.connect() as db:
                    db.execute("UPDATE recipients SET status = 'pending' WHERE status = 'processing'")
            except Exception:
                logger.exception("Could not recover unfinished work")
            stop.wait(1)
