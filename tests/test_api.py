import time
from concurrent.futures import ThreadPoolExecutor
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.worker import process_one


@pytest.fixture
def api(tmp_path):
    app = create_app(str(tmp_path / "test.db"), start_worker=False)
    with TestClient(app) as client:
        yield client, app.state.database


def payload(recipients=None):
    return {"certificate": {"title": "Python Workshop", "issuer": "Learning Club", "issued_on": "2026-10-07"},
            "recipients": recipients if recipients is not None else [{"name": "Anita Rao", "email": "anita@example.com"}]}


def submit(client, body=None):
    response = client.post("/jobs", json=body or payload())
    assert response.status_code == 202
    return response.json()


def test_create_job_and_progress(api):
    client, database = api
    job = submit(client, payload([{"name": "First", "email": "first@example.com"}, {"name": "Second", "email": "second@example.com"}]))
    status = client.get(job["status_url"]).json()
    assert (status["status"], status["pending"], status["progress_percent"]) == ("queued", 2, 0)
    assert process_one(database)
    status = client.get(job["status_url"]).json()
    assert (status["status"], status["succeeded"], status["progress_percent"]) == ("processing", 1, 50)
    assert process_one(database)
    assert not process_one(database)
    status = client.get(job["status_url"]).json()
    assert (status["status"], status["completed"], status["progress_percent"]) == ("completed", 2, 100)


@pytest.mark.parametrize("recipients", [[], [{}] * 1001, "wrong"])
def test_invalid_batch_size_or_type(api, recipients):
    client, database = api
    assert client.post("/jobs", json=payload(recipients)).status_code == 422
    with database.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("field,value", [("title", " "), ("issuer", "x" * 121), ("issued_on", "2026-02-30"), ("title", "bad\x00text")])
def test_invalid_shared_information(api, field, value):
    client, _ = api
    body = payload()
    body["certificate"][field] = value
    assert client.post("/jobs", json=body).status_code == 422


@pytest.mark.parametrize("invalid", [None, 12, "name", {}, {"name": " ", "email": "valid@example.com"},
    {"name": "A", "email": "wrong"}, {"name": "A", "email": ".name@example.com"},
    {"name": "A", "email": "name@-example.com"}, {"name": "A", "email": "a@example.com", "unexpected": 1}])
def test_invalid_recipient_does_not_block_valid_recipient(api, invalid):
    client, database = api
    job = submit(client, payload([invalid, {"name": "Valid", "email": "v@example.com"}]))
    assert (job["accepted"], job["rejected"]) == (1, 1)
    process_one(database)
    status = client.get(job["status_url"]).json()
    assert status["status"] == "completed_with_errors"
    results = client.get(job["results_url"]).json()["items"]
    assert results[0]["position"] == 0
    assert results[0]["error_code"] == "validation_error"
    assert results[0]["download_url"] is None
    assert results[1]["status"] == "succeeded"


def test_all_invalid_is_finished(api):
    client, database = api
    job = submit(client, payload([{}]))
    assert not process_one(database)
    status = client.get(job["status_url"]).json()
    assert status["status"] == "failed"
    assert status["progress_percent"] == 100


def test_generation_and_download_escape_recipient_text(api):
    client, database = api
    name = 'Zoë & <script>alert("x")</script>'
    job = submit(client, payload([{"name": name, "email": "zoe@example.com"}]))
    process_one(database)
    result = client.get(job["results_url"]).json()["items"][0]
    response = client.get(result["download_url"])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "attachment;" in response.headers["content-disposition"]
    svg = ElementTree.fromstring(response.content)
    contents = " ".join(svg.itertext())
    for expected in (name, "Python Workshop", "Learning Club", "2026-10-07", result["id"]):
        assert expected in contents
    assert svg.find(".//{http://www.w3.org/2000/svg}script") is None


def test_individual_generation_failure(api):
    client, database = api
    job = submit(client, payload([{"name": "First", "email": "f@example.com"}, {"name": "Second", "email": "s@example.com"}]))

    def broken_renderer(*args):
        raise RuntimeError("private implementation detail")

    assert process_one(database, broken_renderer)
    assert client.get(job["status_url"]).json()["status"] == "processing"
    assert process_one(database)
    results = client.get(job["results_url"]).json()["items"]
    assert results[0]["error_code"] == "generation_error"
    assert "private" not in results[0]["error"]
    assert results[1]["download_url"]
    assert client.get(job["status_url"]).json()["status"] == "completed_with_errors"


def test_download_not_ready_and_not_found(api):
    client, database = api
    job = submit(client)
    item = client.get(job["results_url"]).json()["items"][0]
    url = f"/jobs/{job['id']}/certificates/{item['id']}"
    assert client.get(url).status_code == 409
    assert client.get("/jobs/missing").status_code == 404
    assert client.get("/jobs/missing/recipients").status_code == 404
    assert client.get(f"/jobs/missing/certificates/{item['id']}").status_code == 404
    process_one(database)
    assert client.get(url).status_code == 200


def test_pagination(api):
    client, _ = api
    job = submit(client, payload([{"name": str(i), "email": "a@example.com"} for i in range(3)]))
    page = client.get(job["results_url"], params={"offset": 1, "limit": 1}).json()
    assert page["total"] == 3
    assert [item["position"] for item in page["items"]] == [1]
    assert client.get(job["results_url"], params={"offset": -1}).status_code == 422
    assert client.get(job["results_url"], params={"limit": 1001}).status_code == 422


def test_concurrent_submissions(api):
    client, database = api
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: submit(client), range(8)))
    assert len({job["id"] for job in jobs}) == 8
    while process_one(database):
        pass
    assert all(client.get(job["status_url"]).json()["status"] == "completed" for job in jobs)


def test_worker_recovers_interrupted_job_and_preserves_output(tmp_path):
    path = str(tmp_path / "persist.db")
    first_app = create_app(path, start_worker=False)
    with TestClient(first_app) as client:
        job = submit(client)
        with first_app.state.database.connect() as db:
            db.execute("UPDATE recipients SET status = 'processing'")
    with TestClient(create_app(path)) as client:
        deadline = time.monotonic() + 5
        while client.get(job["status_url"]).json()["status"] != "completed":
            assert time.monotonic() < deadline, "Worker did not complete the job"
            time.sleep(0.02)
        url = client.get(job["results_url"]).json()["items"][0]["download_url"]
        original = client.get(url).content
    with TestClient(create_app(path)) as client:
        assert client.get(url).content == original


def test_maximum_batch(api):
    client, database = api
    job = submit(client, payload([{"name": f"Participant {i}", "email": f"p{i}@example.com"} for i in range(1000)]))
    assert job["accepted"] == 1000
    while process_one(database):
        pass
    status = client.get(job["status_url"]).json()
    assert status["succeeded"] == 1000
    assert status["progress_percent"] == 100
