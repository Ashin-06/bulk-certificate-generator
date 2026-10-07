import json
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Response
from pydantic import ValidationError

from app.database import Database
from app.schemas import JobRequest, Recipient
from app.worker import run_worker


def create_app(database_path: str | None = None, start_worker: bool = True) -> FastAPI:
    database = Database(database_path or os.getenv("DATABASE_PATH", "data/certificates.db"))

    @asynccontextmanager
    async def lifespan(app):
        database.initialize()
        stop = threading.Event()
        worker = None
        if start_worker:
            worker = threading.Thread(target=run_worker, args=(database, stop), daemon=True)
            worker.start()
        try:
            yield
        finally:
            stop.set()
            if worker:
                worker.join()

    app = FastAPI(title="Bulk Certificate Generator", version="1.0.0", lifespan=lifespan)
    app.state.database = database

    def require_job(db, job_id):
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Job not found")
        return row

    @app.post("/jobs", status_code=202)
    def create_job(payload: JobRequest, response: Response):
        job_id = str(uuid4())
        rows = []
        valid_count = 0
        for position, value in enumerate(payload.recipients):
            recipient_id = str(uuid4())
            try:
                recipient = Recipient.model_validate(value)
            except ValidationError as exc:
                errors = [{"field": ".".join(map(str, error["loc"])), "message": error["msg"]}
                          for error in exc.errors(include_input=False, include_url=False)]
                rows.append((recipient_id, job_id, position, None, None, "failed", "validation_error", json.dumps(errors)))
            else:
                valid_count += 1
                rows.append((recipient_id, job_id, position, recipient.name, recipient.email, "pending", None, None))
        with database.connect() as db:
            db.execute("INSERT INTO jobs VALUES (?, ?, ?, ?, ?)", (
                job_id, payload.certificate.title, payload.certificate.issuer,
                payload.certificate.issued_on.isoformat(), datetime.now(timezone.utc).isoformat()))
            db.executemany("""INSERT INTO recipients
                (id, job_id, position, name, email, status, error_code, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", rows)
        response.headers["Location"] = f"/jobs/{job_id}"
        return {"id": job_id, "total": len(rows), "accepted": valid_count,
                "rejected": len(rows) - valid_count, "status_url": f"/jobs/{job_id}",
                "results_url": f"/jobs/{job_id}/recipients"}

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str):
        with database.connect() as db:
            db.execute("BEGIN")
            job = require_job(db, job_id)
            counts = {key: 0 for key in ("pending", "processing", "succeeded", "failed")}
            for row in db.execute("SELECT status, count(*) AS n FROM recipients WHERE job_id = ? GROUP BY status", (job_id,)):
                counts[row["status"]] = row["n"]
            generation_failures = db.execute("SELECT count(*) FROM recipients WHERE job_id = ? AND error_code = 'generation_error'", (job_id,)).fetchone()[0]
        total = sum(counts.values())
        completed = counts["succeeded"] + counts["failed"]
        if completed == total:
            status = "completed" if not counts["failed"] else ("failed" if not counts["succeeded"] else "completed_with_errors")
        else:
            status = "processing" if counts["processing"] or counts["succeeded"] or generation_failures else "queued"
        return {"id": job_id, "status": status, "created_at": job["created_at"],
                "certificate": {key: job[key] for key in ("title", "issuer", "issued_on")},
                "total": total, "completed": completed, **counts,
                "progress_percent": round(completed / total * 100, 2),
                "results_url": f"/jobs/{job_id}/recipients"}

    @app.get("/jobs/{job_id}/recipients")
    def job_results(job_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
        with database.connect() as db:
            db.execute("BEGIN")
            require_job(db, job_id)
            total = db.execute("SELECT count(*) FROM recipients WHERE job_id = ?", (job_id,)).fetchone()[0]
            rows = db.execute("""SELECT id, position, name, email, status, error_code, error
                FROM recipients WHERE job_id = ? ORDER BY position LIMIT ? OFFSET ?""", (job_id, limit, offset)).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            if item["error_code"] == "validation_error":
                item["error"] = json.loads(item["error"])
            item["download_url"] = f"/jobs/{job_id}/certificates/{row['id']}" if row["status"] == "succeeded" else None
            items.append(item)
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    @app.get("/jobs/{job_id}/certificates/{certificate_id}")
    def download_certificate(job_id: str, certificate_id: str):
        with database.connect() as db:
            row = db.execute("SELECT status, svg FROM recipients WHERE id = ? AND job_id = ?", (certificate_id, job_id)).fetchone()
        if row is None:
            raise HTTPException(404, "Certificate not found")
        if row["status"] != "succeeded":
            raise HTTPException(409, "Certificate is not available")
        return Response(row["svg"], media_type="image/svg+xml", headers={
            "Content-Disposition": f'attachment; filename="{certificate_id}.svg"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        })

    return app


app = create_app()
