# Bulk Certificate Generator

A FastAPI backend that accepts up to 1,000 recipients in one request, generates certificates in the background, and stores job results in SQLite.

Each recipient gets a separate result. Invalid recipients are marked as failed while valid recipients continue. Certificates use one predefined SVG template and can be downloaded individually.

## Requirements

- Python 3.12
- No separate database server or message broker is required.

## Setup

From the project directory, create a virtual environment:

```sh
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```sh
source .venv/bin/activate
```

Install dependencies and start the application:

```sh
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open [interactive API documentation](http://127.0.0.1:8000/docs) to submit and inspect requests. The database is created automatically at `data/certificates.db`. Set `DATABASE_PATH` to use a different location.

**Run one application process per database.** The worker runs inside that process and recovers interrupted items at startup. Multiple processes or replicas sharing the database are not supported. Stop the previous process before restarting it.

## Run tests

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Tests use temporary databases. They cover job creation, validation, partial success, generation errors, progress, SVG contents and escaping, downloads, pagination, concurrent submissions, persistence, startup recovery, and a 1,000-recipient batch. GitHub Actions runs the suite on pushes and pull requests.

## Submit a batch

```sh
curl -X POST http://127.0.0.1:8000/jobs -H "Content-Type: application/json" --data-binary @examples/request.json
```

On Windows PowerShell, use `curl.exe` in place of `curl` for these examples.

Example request:

```json
{
  "certificate": {
    "title": "Python Workshop",
    "issuer": "Learning Club",
    "issued_on": "2026-10-07"
  },
  "recipients": [
    {"name": "Anita Rao", "email": "anita@example.com"},
    {"name": "Rahul Shah", "email": "rahul@example.com"},
    {"name": "", "email": "invalid"}
  ]
}
```

The API responds with `202 Accepted` and a `Location` header. For this example, `accepted` is 2 and `rejected` is 1:

```json
{
  "id": "<job-id>",
  "total": 3,
  "accepted": 2,
  "rejected": 1,
  "status_url": "/jobs/<job-id>",
  "results_url": "/jobs/<job-id>/recipients"
}
```

`accepted` counts valid recipients, not finished certificates. Keep the returned job ID to check progress. Repeated POST requests create separate jobs; duplicate recipients are treated as separate entries, identified by their zero-based `position`.

## Check progress and results

Replace `<job-id>` with the returned ID:

```sh
curl http://127.0.0.1:8000/jobs/<job-id>
curl "http://127.0.0.1:8000/jobs/<job-id>/recipients?offset=0&limit=100"
```

The status response includes `total`, `pending`, `processing`, `succeeded`, `failed`, `completed`, and `progress_percent`. Completed means succeeded or failed, including validation failures. Progress reaching 100% means all recipients have a final result; it does not mean every certificate succeeded.

| Job status | Meaning |
| --- | --- |
| `queued` | Valid recipients are waiting for generation. |
| `processing` | Generation has started and recipients remain unfinished. |
| `completed` | Every certificate succeeded. |
| `completed_with_errors` | Some recipients succeeded and some failed. |
| `failed` | Every recipient failed. |

An all-invalid batch is still recorded and is immediately `failed` with 100% progress. Poll the status endpoint until a terminal status is returned. The worker may finish small batches before the first poll.

The paginated recipients endpoint returns each entry's ID, input position, validated name and email, status, error details, and download URL. Invalid entries have null name/email; their position links the error back to the original input. Validation errors include the affected field and message. Generation failures use `generation_error` and a generic message; exception details remain in server logs.

## Download a certificate

Use a successful recipient's `download_url`:

```sh
curl http://127.0.0.1:8000/jobs/<job-id>/certificates/<certificate-id> -o certificate.svg
```

Open the SVG in a browser or image viewer. It contains the recipient name, course/event title, issuer, issue date, and certificate ID. Email is stored as contact information and is not printed on the certificate. The API does not send email.

## Endpoints and validation

| Method | Endpoint | Result |
| --- | --- | --- |
| POST | `/jobs` | Accept a batch; return `202`. |
| GET | `/jobs/{job_id}` | Job status and aggregate progress. |
| GET | `/jobs/{job_id}/recipients` | Results in input order; `offset` and `limit` pagination. |
| GET | `/jobs/{job_id}/certificates/{certificate_id}` | SVG attachment for a successful recipient. |

- A batch requires 1–1,000 entries. Invalid shared certificate information rejects the entire request with `422`; no job is created.
- Title, issuer, and recipient name must be printable, nonblank strings of at most 120 characters. Surrounding whitespace is removed. Issue date must be a valid date.
- Each recipient requires a name and an email address. Email validation supports common ASCII addresses with a dotted domain; it is not a full RFC mailbox parser and does not verify deliverability.
- Unknown fields are rejected to catch misspelled inputs. Recipient-level problems affect only that entry, even when an entry is not an object.
- Pagination defaults to 100 entries and allows 1–1,000 per page.
- Unknown jobs or certificates return `404`. Pending or failed certificates return `409` when requested for download.

## Implementation decisions

**Background processing.** Returning after validation and database insertion keeps the request from waiting for every certificate. One worker thread claims pending recipients and handles them sequentially. SQLite is the persistent queue; there is no in-memory job list to lose on shutdown. Idle polling runs every 200 ms.

**Relational storage.** `jobs` holds common certificate information. `recipients` references its job and holds each entry's position, status, error, and generated SVG. The input batch is inserted in one transaction. The generated document and its successful status are committed together, preventing a result from claiming success without a document. SQLite WAL mode allows readers while writes are taking place; writes remain serialized.

**Failure isolation and recovery.** A generation exception marks only that recipient as failed. The next recipient is still processed. On startup, items left in `processing` are reset to `pending`. A crash after rendering but before saving can cause that item to be rendered again; its ID stays the same. Successful certificates are not regenerated. Failed entries are not retried automatically; submit corrected or failed recipients in a new batch if needed.

**SVG output.** SVG keeps the template small, supports Unicode text through browser fonts, and requires no PDF library or font installation. All inserted text is escaped, and the template contains no scripts or external resources. Long lines are compressed to stay within the border. Appearance of less common scripts depends on the viewer's fonts. The template can be changed in `app/certificates.py` without changing the API or queue.

**Status calculation.** Progress is derived from recipient records in a read transaction, avoiding a separate counter that could drift. Recipient results are paginated and exclude document contents so polling does not repeatedly transfer the certificates.

## Scope and limits

This is a local assignment service, with no authentication or ownership checks. Job IDs are identifiers, not access control. Do not expose it publicly with real recipient information without adding authentication, authorization, TLS, request-size/rate limits, and a retention policy. There is no automatic deletion of stored names, emails, or certificates.

Each request is capped at 1,000 recipients, but total queue and database size are not capped. SQLite and the single worker suit a small deployment. A larger service would use a dedicated worker queue, PostgreSQL, retry policies, and document storage outside the database. The current implementation intentionally avoids those extra services for straightforward setup and review.

## Code layout

```text
app/
  main.py           HTTP endpoints and application lifecycle
  schemas.py        Batch and recipient validation
  database.py       SQLite connections and schema
  worker.py         Queue processing and recovery
  certificates.py   Predefined certificate template
tests/test_api.py   API, generation, and recovery tests
examples/request.json
```

Start with `create_job` in `app/main.py`, then follow `process_one` in `app/worker.py`. To change the printed fields, update the request schema, database columns, and template together. To change output format, replace the renderer and update document storage, download headers, and tests.

The worker uses FastAPI's [lifespan mechanism](https://fastapi.tiangolo.com/advanced/events/); its integration tests follow the documented [TestClient lifespan pattern](https://fastapi.tiangolo.com/advanced/testing-events/).
