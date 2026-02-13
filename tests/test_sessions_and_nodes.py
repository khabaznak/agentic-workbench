from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from interfaces.http.main import app


def _set_test_db(tmp_path: Path) -> None:
    test_db = tmp_path / "atrium_test.db"
    app.dependency_overrides = {}
    # startup event reads this env variable.
    import os

    os.environ["ATRIUM_DB_PATH"] = str(test_db)


def test_create_and_list_sessions(tmp_path: Path) -> None:
    _set_test_db(tmp_path)
    with TestClient(app) as client:
        create_resp = client.post("/api/sessions", json={"name": "Session A"})
        assert create_resp.status_code == 201
        created = create_resp.json()
        assert created["name"] == "Session A"

        list_resp = client.get("/api/sessions")
        assert list_resp.status_code == 200
        sessions = list_resp.json()
        assert len(sessions) == 1
        assert sessions[0]["id"] == created["id"]


def test_node_crud_for_existing_session(tmp_path: Path) -> None:
    _set_test_db(tmp_path)
    with TestClient(app) as client:
        session_resp = client.post("/api/sessions", json={"name": "Session B"})
        session_id = session_resp.json()["id"]

        create_node_resp = client.post(
            "/api/nodes",
            json={
                "session_id": session_id,
                "type": "question",
                "title": "Choose API contract",
                "status": "open",
                "rationale": "Need consistent event shape",
                "owner": "human",
                "priority": 2,
            },
        )
        assert create_node_resp.status_code == 201
        node = create_node_resp.json()
        assert node["title"] == "Choose API contract"

        patch_resp = client.patch(
            f"/api/nodes/{node['id']}",
            json={"status": "in_progress", "owner": "agent:codex"},
        )
        assert patch_resp.status_code == 200
        patched = patch_resp.json()
        assert patched["status"] == "in_progress"
        assert patched["owner"] == "agent:codex"


def test_session_page_renders_and_form_creates(tmp_path: Path) -> None:
    _set_test_db(tmp_path)
    with TestClient(app) as client:
        page = client.get("/sessions")
        assert page.status_code == 200
        assert "Decision Sessions" in page.text

        post_form = client.post("/sessions", data={"name": "Session C"})
        assert post_form.status_code == 200
        assert "Session C" in post_form.text
