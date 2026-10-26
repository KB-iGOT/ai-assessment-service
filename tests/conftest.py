"""
Shared fixtures.

Nothing here talks to a real service. The environment is pinned before any
`assessment` module is imported, so a developer's local `.env` cannot change
what the tests see (`load_dotenv` never overrides a variable that is already
set), and `GOOGLE_APPLICATION_CREDENTIALS` is blanked so no GenAI client is
built at import time.
"""

import copy
import os
import sys
import types

os.environ.update({
    "KARMAYOGI_API_KEY": "Bearer test-key",
    "SUNBIRD_SSO_URL": "https://sso.test/",
    "SUNBIRD_SSO_REALM": "test-realm",
    "GOOGLE_APPLICATION_CREDENTIALS": "",
    "LANGFUSE_ENABLED": "false",
    "DISABLE_AUTH_VERIFICATION": "false",
    "DOCUMENT_STORAGE_TYPE": "local",
    "NORMALIZE_OPTION_INDEX_BASE": "true",
    "TOPUP_MAX_ROUNDS": "1",
    "QUESTION_BATCH_SIZE": "25",
})

# WeasyPrint needs native Pango/GObject libraries that a CI agent or a Windows
# laptop may not have. `exporters` imports it at module level, so without this
# stub the whole API module fails to import. Only used when the real import
# fails; PDF rendering itself is mocked in the download tests.
try:
    import weasyprint  # noqa: F401
except Exception:  # pragma: no cover - depends on the machine
    _wp = types.ModuleType("weasyprint")
    _wp.HTML = object
    _wp.CSS = object
    _wp_text = types.ModuleType("weasyprint.text")
    _wp_fonts = types.ModuleType("weasyprint.text.fonts")
    _wp_fonts.FontConfiguration = object
    sys.modules.update({
        "weasyprint": _wp,
        "weasyprint.text": _wp_text,
        "weasyprint.text.fonts": _wp_fonts,
    })

import pytest  # noqa: E402

from factories import USER_ID, make_assessment  # noqa: E402


@pytest.fixture
def assessment():
    return make_assessment()


# --------------------------------------------------------------------------
# Fake persistence / Kafka / storage for the API
# --------------------------------------------------------------------------

class FakeDB:
    """In-memory stand-in for the db.py functions api.py imports."""

    def __init__(self):
        self.rows = {}
        self.created_jobs = []
        self.completed_jobs = []
        self.saves = []
        self.history = []
        self.templates = {}
        self.force_conflict = False

    def add_row(self, job_id, *, user_id=USER_ID, status="COMPLETED", data=None,
                version=1, metadata=None, ai_original=None, edited_at=None):
        data = make_assessment() if data is None else data
        row = {
            "course_id": job_id,
            "job_id": job_id,
            "user_id": user_id,
            "status": status,
            "assessment_data": copy.deepcopy(data),
            "ai_original_data": copy.deepcopy(ai_original) if ai_original else None,
            "version": version,
            "metadata": metadata if metadata is not None else {"config": {"enable_blooms": True}},
            "edited_at": edited_at,
            "token_usage": {},
        }
        self.rows[job_id] = row
        return row

    # -- db.py API --------------------------------------------------------
    async def get_assessment_status(self, job_id):
        row = self.rows.get(job_id)
        return copy.deepcopy(row) if row else None

    async def find_job_by_prefix(self, prefix):
        for key, row in self.templates.items():
            if key.startswith(prefix):
                return copy.deepcopy(row)
        return None

    async def create_job(self, job_id, user_id=None, metadata=None):
        self.created_jobs.append({"job_id": job_id, "user_id": user_id, "metadata": metadata})

    async def create_completed_job(self, job_id, user_id, meta, data, usage):
        self.completed_jobs.append({"job_id": job_id, "user_id": user_id,
                                    "metadata": meta, "data": data, "usage": usage})

    async def save_edited_assessment(self, job_id, user_id, expected_version, data, audit_rows):
        row = self.rows[job_id]
        if self.force_conflict or int(row["version"]) != int(expected_version):
            return None
        row["version"] = int(row["version"]) + 1
        row["assessment_data"] = copy.deepcopy(data)
        self.saves.append({"job_id": job_id, "user_id": user_id,
                           "expected_version": expected_version,
                           "data": copy.deepcopy(data), "audit_rows": audit_rows})
        return row["version"]

    async def get_user_assessments_history(self, user_id):
        return copy.deepcopy(self.history)


class FakeStorage:
    def __init__(self):
        self.saved = []

    def save_file(self, fileobj, filename, job_id):
        content = fileobj.read()
        path = f"uploads/{job_id}/{filename}"
        self.saved.append({"filename": filename, "job_id": job_id, "content": content})
        return path, len(content)


@pytest.fixture
def fake_db(monkeypatch):
    import assessment.api as api

    db = FakeDB()
    for name in ("get_assessment_status", "find_job_by_prefix", "create_job",
                 "create_completed_job", "save_edited_assessment",
                 "get_user_assessments_history"):
        monkeypatch.setattr(api, name, getattr(db, name))
    return db


@pytest.fixture
def sent_events(monkeypatch):
    import assessment.api as api

    events = []

    async def _send(payload):
        events.append(payload)

    monkeypatch.setattr(api, "send_request_event", _send)
    return events


@pytest.fixture
def fake_storage(monkeypatch):
    import assessment.api as api

    storage = FakeStorage()
    monkeypatch.setattr(api, "get_storage_service", lambda: storage)
    return storage


@pytest.fixture
def current_user():
    """Mutable holder so a test can switch the authenticated user."""
    return {"id": USER_ID}


@pytest.fixture
def client(fake_db, sent_events, fake_storage, current_user):
    """
    TestClient with auth overridden. Used without a `with` block on purpose, so
    the lifespan (DB pool, SSO env check, cleanup scheduler) never runs.
    """
    from fastapi.testclient import TestClient

    import assessment.api as api
    from assessment.auth import get_current_user

    api.app.dependency_overrides[get_current_user] = lambda: current_user["id"]
    try:
        yield TestClient(api.app)
    finally:
        api.app.dependency_overrides.clear()
