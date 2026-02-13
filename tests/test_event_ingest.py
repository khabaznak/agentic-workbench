from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from interfaces.http.main import app


def _set_test_db(tmp_path: Path) -> None:
    os.environ["ATRIUM_DB_PATH"] = str(tmp_path / "atrium_events_test.db")


def test_event_ingest_maps_to_graph_state(tmp_path: Path) -> None:
    _set_test_db(tmp_path)

    with TestClient(app) as client:
        question_1 = client.post(
            "/api/events",
            json={
                "source": "mcp",
                "event_type": "question_presented",
                "session_external_id": "session-001",
                "agent_name": "codex",
                "payload": {
                    "node_ref": "q-1",
                    "title": "Choose ingestion strategy",
                    "context_prompt": "We need automatic capture first.",
                    "choices": [
                        {"label": "A", "text": "Manual only"},
                        {"label": "B", "text": "MCP event endpoint"},
                        {"label": "C", "text": "Transcript parser first"},
                    ],
                },
            },
        )
        assert question_1.status_code == 201
        body_1 = question_1.json()
        session_id = body_1["session_id"]

        choose = client.post(
            "/api/events",
            json={
                "source": "mcp",
                "event_type": "choice_selected",
                "session_external_id": "session-001",
                "payload": {
                    "question_node_ref": "q-1",
                    "choice_label": "B",
                },
            },
        )
        assert choose.status_code == 201

        note = client.post(
            "/api/events",
            json={
                "source": "mcp",
                "event_type": "note_added",
                "session_external_id": "session-001",
                "payload": {
                    "node_ref": "q-1",
                    "note": "Selected MCP to reduce friction.",
                },
            },
        )
        assert note.status_code == 201

        question_2 = client.post(
            "/api/events",
            json={
                "source": "mcp",
                "event_type": "question_presented",
                "session_external_id": "session-001",
                "payload": {
                    "node_ref": "q-2",
                    "parent_node_ref": "q-1",
                    "title": "How to map events?",
                    "choices": [
                        {"label": "A", "text": "Loose payload"},
                        {"label": "B", "text": "Strict event contract"},
                    ],
                },
            },
        )
        assert question_2.status_code == 201

        status_change = client.post(
            "/api/events",
            json={
                "source": "mcp",
                "event_type": "node_status_changed",
                "session_external_id": "session-001",
                "payload": {
                    "node_ref": "q-2",
                    "status": "blocked",
                },
            },
        )
        assert status_change.status_code == 201

        graph = client.get(f"/api/sessions/{session_id}/graph")
        assert graph.status_code == 200
        graph_json = graph.json()

        assert graph_json["session"]["external_id"] == "session-001"
        assert len(graph_json["nodes"]) == 2
        assert len(graph_json["edges"]) == 1

        nodes_by_ref = {node["external_ref"]: node for node in graph_json["nodes"]}
        assert nodes_by_ref["q-1"]["owner"] == "agent:codex"
        assert "Selected MCP" in nodes_by_ref["q-1"]["rationale"]
        assert nodes_by_ref["q-2"]["status"] == "blocked"

        q1_id = nodes_by_ref["q-1"]["id"]
        q1_choices = [c for c in graph_json["choices"] if c["node_id"] == q1_id]
        chosen = [c for c in q1_choices if c["is_chosen"]]
        assert len(chosen) == 1
        assert chosen[0]["label"] == "B"

        edge = graph_json["edges"][0]
        assert edge["from_node_id"] == nodes_by_ref["q-1"]["id"]
        assert edge["to_node_id"] == nodes_by_ref["q-2"]["id"]


def test_choice_selected_falls_back_to_latest_question(tmp_path: Path) -> None:
    _set_test_db(tmp_path)

    with TestClient(app) as client:
        first = client.post(
            "/api/events",
            json={
                "event_type": "question_presented",
                "session_external_id": "session-002",
                "payload": {
                    "node_ref": "q-latest",
                    "title": "Choose next step",
                    "choices": ["Option A", "Option B"],
                },
            },
        )
        assert first.status_code == 201
        session_id = first.json()["session_id"]

        choose = client.post(
            "/api/events",
            json={
                "event_type": "choice_selected",
                "session_external_id": "session-002",
                "payload": {
                    "choice_label": "A",
                },
            },
        )
        assert choose.status_code == 201

        graph = client.get(f"/api/sessions/{session_id}/graph")
        assert graph.status_code == 200
        graph_json = graph.json()

        node_id = graph_json["nodes"][0]["id"]
        chosen = [c for c in graph_json["choices"] if c["node_id"] == node_id and c["is_chosen"]]
        assert len(chosen) == 1
        assert chosen[0]["label"] == "A"


def test_graph_filters_and_replay_prompt(tmp_path: Path) -> None:
    _set_test_db(tmp_path)

    with TestClient(app) as client:
        base = client.post(
            "/api/events",
            json={
                "event_type": "question_presented",
                "session_external_id": "session-003",
                "payload": {
                    "node_ref": "q-filter-1",
                    "title": "Choose rollout path",
                    "context_prompt": "Current path is conservative.",
                    "choices": [
                        {"label": "A", "text": "Conservative"},
                        {"label": "B", "text": "Aggressive"},
                    ],
                },
            },
        )
        assert base.status_code == 201
        session_id = base.json()["session_id"]
        node_id = base.json()["affected_node_id"]

        client.post(
            "/api/events",
            json={
                "event_type": "choice_selected",
                "session_external_id": "session-003",
                "payload": {
                    "question_node_ref": "q-filter-1",
                    "choice_label": "A",
                },
            },
        )

        client.post(
            "/api/events",
            json={
                "event_type": "question_presented",
                "session_external_id": "session-003",
                "payload": {
                    "node_ref": "q-filter-2",
                    "title": "Follow-up question",
                    "choices": ["One", "Two"],
                },
            },
        )
        client.post(
            "/api/events",
            json={
                "event_type": "node_status_changed",
                "session_external_id": "session-003",
                "payload": {
                    "node_ref": "q-filter-2",
                    "status": "done",
                },
            },
        )

        filtered_status = client.get(f"/api/sessions/{session_id}/graph?status=done")
        assert filtered_status.status_code == 200
        done_nodes = filtered_status.json()["nodes"]
        assert len(done_nodes) == 1
        assert done_nodes[0]["external_ref"] == "q-filter-2"

        filtered_unchosen = client.get(f"/api/sessions/{session_id}/graph?unchosen_only=true")
        assert filtered_unchosen.status_code == 200
        unchosen_nodes = filtered_unchosen.json()["nodes"]
        assert len(unchosen_nodes) == 2

        replay = client.get(f"/api/nodes/{node_id}/replay-prompt?choice_label=B")
        assert replay.status_code == 200
        prompt = replay.json()["prompt"]
        assert "Decision point: Choose rollout path" in prompt
        assert "Previously chosen path: A: Conservative" in prompt
        assert "Alternative to execute now: B: Aggressive" in prompt
