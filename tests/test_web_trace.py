"""Web UI renders stored AIOS business_trace_id for an org-scoped user."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


def _register(client, prefix):
    slug = f"{prefix}-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/v1/auth/register",
        json={"email": f"{slug}@arvo.test", "password": "secret123", "org_name": slug, "org_slug": slug},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return body["access_token"], body["org_id"], f"{slug}@arvo.test"


async def test_web_pages_render_business_trace_id(client):
    tok, org_id, email = _register(client, "trace")
    h = {"Authorization": f"Bearer {tok}"}
    trace = f"trace-{uuid.uuid4().hex[:10]}"

    created = client.post(
        "/api/v1/findings",
        json={"title": f"Traced {uuid.uuid4().hex[:8]}", "exposure_amount": "1"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    finding_id = created.json()["id"]

    async def _attach_trace():
        from app.database.engine import get_sessionmaker
        from app.database.models import Finding, FindingEvidence

        async with get_sessionmaker()() as s:
            row = await s.get(Finding, finding_id)
            assert row is not None and row.org_id == org_id
            row.provenance = {"source": "aios", "business_trace_id": trace}
            s.add(FindingEvidence(
                finding_id=finding_id,
                kind="EVIDENCE",
                content={"source": "aios", "business_trace_id": trace},
            ))
            await s.commit()

    await _attach_trace()

    assert client.post("/login", data={"email": email, "password": "secret123"}).status_code == 302
    for path in ("/findings", "/evidence"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert trace in resp.text, f"{path} did not render business_trace_id {trace}"
