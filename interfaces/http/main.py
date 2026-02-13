from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Literal

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from interfaces.http.db import get_conn, init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Atrium", lifespan=lifespan)
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

NodeType = Literal["question", "decision", "task"]
NodeStatus = Literal["open", "in_progress", "blocked", "done"]
EventType = Literal[
    "question_presented",
    "choice_selected",
    "note_added",
    "node_status_changed",
]


class SessionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    external_id: str | None = None


class SessionOut(BaseModel):
    id: int
    external_id: str | None
    name: str
    started_at: str | None
    ended_at: str | None
    created_at: str


class NodeCreate(BaseModel):
    session_id: int
    type: NodeType
    title: str = Field(min_length=1, max_length=300)
    status: NodeStatus = "open"
    rationale: str | None = None
    owner: str | None = None
    priority: int | None = None
    context_prompt: str | None = None
    external_ref: str | None = None


class NodeUpdate(BaseModel):
    status: NodeStatus | None = None
    rationale: str | None = None
    owner: str | None = None
    priority: int | None = None


class NodeOut(BaseModel):
    id: int
    session_id: int
    external_ref: str | None
    type: NodeType
    title: str
    status: NodeStatus
    rationale: str | None
    owner: str | None
    priority: int | None
    context_prompt: str | None
    created_at: str
    updated_at: str


class ChoiceOut(BaseModel):
    id: int
    node_id: int
    label: str
    text: str
    is_chosen: bool
    chosen_at: str | None


class EdgeOut(BaseModel):
    id: int
    from_node_id: int
    to_node_id: int
    type: str
    created_at: str


class SessionGraphOut(BaseModel):
    session: SessionOut
    nodes: list[NodeOut]
    edges: list[EdgeOut]
    choices: list[ChoiceOut]


class EventIn(BaseModel):
    source: str = "mcp"
    event_type: EventType
    session_external_id: str = Field(min_length=1, max_length=200)
    agent_name: str | None = None
    timestamp: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventIngestOut(BaseModel):
    event_log_id: int
    session_id: int
    affected_node_id: int | None


def _rows_to_sessions(rows: list) -> list[SessionOut]:
    return [
        SessionOut(
            id=row["id"],
            external_id=row["external_id"],
            name=row["name"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            created_at=row["created_at"],
        )
        for row in rows
    ]


def _row_to_node(row) -> NodeOut:
    return NodeOut(
        id=row["id"],
        session_id=row["session_id"],
        external_ref=row["external_ref"],
        type=row["type"],
        title=row["title"],
        status=row["status"],
        rationale=row["rationale"],
        owner=row["owner"],
        priority=row["priority"],
        context_prompt=row["context_prompt"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return sessions_page(request)


@app.get("/sessions", response_class=HTMLResponse)
def sessions_page(request: Request) -> HTMLResponse:
    sessions = list_sessions()
    return templates.TemplateResponse(
        request,
        "sessions.html",
        {"sessions": sessions},
    )


@app.get("/sessions/{session_id}", response_class=HTMLResponse)
def session_workspace_page(request: Request, session_id: int) -> HTMLResponse:
    graph = get_session_graph(session_id)
    return templates.TemplateResponse(
        request,
        "session_workspace.html",
        {
            "session": graph.session,
            "graph": graph,
        },
    )


@app.get("/sessions/{session_id}/nodes/{node_id}/panel", response_class=HTMLResponse)
def node_detail_panel(request: Request, session_id: int, node_id: int) -> HTMLResponse:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                id, session_id, external_ref, type, title, status,
                rationale, owner, priority, context_prompt,
                created_at, updated_at
            FROM nodes
            WHERE id = ? AND session_id = ?
            """,
            (node_id, session_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Node not found")

        choices = conn.execute(
            """
            SELECT id, node_id, label, text, is_chosen, chosen_at
            FROM choices
            WHERE node_id = ?
            ORDER BY id ASC
            """,
            (node_id,),
        ).fetchall()

    return templates.TemplateResponse(
        request,
        "partials/node_detail_panel.html",
        {
            "node": _row_to_node(row),
            "choices": [
                ChoiceOut(
                    id=choice["id"],
                    node_id=choice["node_id"],
                    label=choice["label"],
                    text=choice["text"],
                    is_chosen=bool(choice["is_chosen"]),
                    chosen_at=choice["chosen_at"],
                )
                for choice in choices
            ],
        },
    )


@app.post("/sessions", response_class=HTMLResponse)
def create_session_form(request: Request, name: str = Form(...)) -> HTMLResponse:
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="Session name is required")

    create_session(SessionCreate(name=cleaned))
    sessions = list_sessions()
    return templates.TemplateResponse(
        request,
        "partials/session_list.html",
        {"sessions": sessions},
    )


@app.get("/api/sessions", response_model=list[SessionOut])
def list_sessions() -> list[SessionOut]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, external_id, name, started_at, ended_at, created_at
            FROM sessions
            ORDER BY datetime(created_at) DESC, id DESC
            """
        ).fetchall()
    return _rows_to_sessions(rows)


@app.post("/api/sessions", response_model=SessionOut, status_code=201)
def create_session(payload: SessionCreate) -> SessionOut:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        with get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions (external_id, name, started_at)
                VALUES (?, ?, ?)
                """,
                (payload.external_id, payload.name.strip(), now),
            )
            session_id = cursor.lastrowid
            row = conn.execute(
                """
                SELECT id, external_id, name, started_at, ended_at, created_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Session external_id already exists") from exc
    return _rows_to_sessions([row])[0]


@app.get("/api/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int) -> SessionOut:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, external_id, name, started_at, ended_at, created_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _rows_to_sessions([row])[0]


@app.get("/api/sessions/{session_id}/graph", response_model=SessionGraphOut)
def get_session_graph(session_id: int) -> SessionGraphOut:
    with get_conn() as conn:
        session_row = conn.execute(
            """
            SELECT id, external_id, name, started_at, ended_at, created_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session_row is None:
            raise HTTPException(status_code=404, detail="Session not found")

        node_rows = conn.execute(
            """
            SELECT
                id, session_id, external_ref, type, title, status,
                rationale, owner, priority, context_prompt,
                created_at, updated_at
            FROM nodes
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (session_id,),
        ).fetchall()
        edge_rows = conn.execute(
            """
            SELECT e.id, e.from_node_id, e.to_node_id, e.type, e.created_at
            FROM edges e
            JOIN nodes n ON n.id = e.from_node_id
            WHERE n.session_id = ?
            ORDER BY e.id ASC
            """,
            (session_id,),
        ).fetchall()
        choice_rows = conn.execute(
            """
            SELECT c.id, c.node_id, c.label, c.text, c.is_chosen, c.chosen_at
            FROM choices c
            JOIN nodes n ON n.id = c.node_id
            WHERE n.session_id = ?
            ORDER BY c.node_id, c.id
            """,
            (session_id,),
        ).fetchall()

    return SessionGraphOut(
        session=_rows_to_sessions([session_row])[0],
        nodes=[_row_to_node(row) for row in node_rows],
        edges=[
            EdgeOut(
                id=row["id"],
                from_node_id=row["from_node_id"],
                to_node_id=row["to_node_id"],
                type=row["type"],
                created_at=row["created_at"],
            )
            for row in edge_rows
        ],
        choices=[
            ChoiceOut(
                id=row["id"],
                node_id=row["node_id"],
                label=row["label"],
                text=row["text"],
                is_chosen=bool(row["is_chosen"]),
                chosen_at=row["chosen_at"],
            )
            for row in choice_rows
        ],
    )


@app.post("/api/nodes", response_model=NodeOut, status_code=201)
def create_node(payload: NodeCreate) -> NodeOut:
    with get_conn() as conn:
        session_row = conn.execute(
            "SELECT id FROM sessions WHERE id = ?",
            (payload.session_id,),
        ).fetchone()
        if session_row is None:
            raise HTTPException(status_code=404, detail="Session not found")

        cursor = conn.execute(
            """
            INSERT INTO nodes (
                session_id, type, title, status, rationale, owner, priority, context_prompt, external_ref
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.session_id,
                payload.type,
                payload.title.strip(),
                payload.status,
                payload.rationale,
                payload.owner,
                payload.priority,
                payload.context_prompt,
                payload.external_ref,
            ),
        )
        node_id = cursor.lastrowid
        row = conn.execute(
            """
            SELECT
                id, session_id, external_ref, type, title, status,
                rationale, owner, priority, context_prompt,
                created_at, updated_at
            FROM nodes
            WHERE id = ?
            """,
            (node_id,),
        ).fetchone()
    return _row_to_node(row)


@app.get("/api/nodes/{node_id}", response_model=NodeOut)
def get_node(node_id: int) -> NodeOut:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT
                id, session_id, external_ref, type, title, status,
                rationale, owner, priority, context_prompt,
                created_at, updated_at
            FROM nodes
            WHERE id = ?
            """,
            (node_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Node not found")
    return _row_to_node(row)


@app.patch("/api/nodes/{node_id}", response_model=NodeOut)
def update_node(node_id: int, payload: NodeUpdate) -> NodeOut:
    updates: list[str] = []
    values: list[object] = []
    body = payload.model_dump(exclude_unset=True)

    for field in ("status", "rationale", "owner", "priority"):
        if field in body:
            updates.append(f"{field} = ?")
            values.append(body[field])

    if not updates:
        raise HTTPException(status_code=400, detail="No updatable fields provided")

    updates.append("updated_at = datetime('now')")
    values.append(node_id)

    with get_conn() as conn:
        current = conn.execute("SELECT id FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if current is None:
            raise HTTPException(status_code=404, detail="Node not found")

        conn.execute(
            f"UPDATE nodes SET {', '.join(updates)} WHERE id = ?",
            tuple(values),
        )
        row = conn.execute(
            """
            SELECT
                id, session_id, external_ref, type, title, status,
                rationale, owner, priority, context_prompt,
                created_at, updated_at
            FROM nodes
            WHERE id = ?
            """,
            (node_id,),
        ).fetchone()
    return _row_to_node(row)


@app.post("/api/events", response_model=EventIngestOut, status_code=201)
def ingest_event(payload: EventIn) -> EventIngestOut:
    with get_conn() as conn:
        session_id = _get_or_create_session_id(conn, payload.session_external_id)
        affected_node_id = _apply_event(conn, session_id, payload)

        event_row = conn.execute(
            """
            INSERT INTO event_log (session_id, source, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                session_id,
                payload.source,
                payload.event_type,
                json.dumps(payload.model_dump(mode="json"), sort_keys=True),
            ),
        )
    return EventIngestOut(
        event_log_id=event_row.lastrowid,
        session_id=session_id,
        affected_node_id=affected_node_id,
    )


def _get_or_create_session_id(conn: sqlite3.Connection, session_external_id: str) -> int:
    row = conn.execute(
        "SELECT id FROM sessions WHERE external_id = ?",
        (session_external_id,),
    ).fetchone()
    if row is not None:
        return int(row["id"])

    now = datetime.now(UTC).isoformat(timespec="seconds")
    created = conn.execute(
        """
        INSERT INTO sessions (external_id, name, started_at)
        VALUES (?, ?, ?)
        """,
        (session_external_id, f"Session {session_external_id}", now),
    )
    return int(created.lastrowid)


def _apply_event(conn: sqlite3.Connection, session_id: int, payload: EventIn) -> int | None:
    if payload.event_type == "question_presented":
        return _apply_question_presented(conn, session_id, payload)
    if payload.event_type == "choice_selected":
        return _apply_choice_selected(conn, session_id, payload)
    if payload.event_type == "note_added":
        return _apply_note_added(conn, session_id, payload)
    if payload.event_type == "node_status_changed":
        return _apply_node_status_changed(conn, session_id, payload)
    raise HTTPException(status_code=400, detail="Unsupported event type")


def _apply_question_presented(
    conn: sqlite3.Connection, session_id: int, event: EventIn
) -> int:
    event_payload = event.payload
    title = str(event_payload.get("title", "")).strip()
    if not title:
        raise HTTPException(status_code=400, detail="question_presented requires payload.title")

    node_ref = _clean_ref(event_payload.get("node_ref"))
    context_prompt = _clean_ref(event_payload.get("context_prompt"))
    rationale = _clean_ref(event_payload.get("rationale"))
    owner = f"agent:{event.agent_name}" if event.agent_name else None

    cursor = conn.execute(
        """
        INSERT INTO nodes (
            session_id, type, title, status, rationale, owner, context_prompt, external_ref
        )
        VALUES (?, 'question', ?, 'open', ?, ?, ?, ?)
        """,
        (session_id, title, rationale, owner, context_prompt, node_ref),
    )
    node_id = int(cursor.lastrowid)

    choices = event_payload.get("choices", [])
    if not isinstance(choices, list):
        raise HTTPException(status_code=400, detail="payload.choices must be a list")

    for index, item in enumerate(choices):
        label, text = _normalize_choice(item, index)
        if text:
            conn.execute(
                """
                INSERT OR REPLACE INTO choices (node_id, label, text)
                VALUES (?, ?, ?)
                """,
                (node_id, label, text),
            )

    parent_ref = _clean_ref(event_payload.get("parent_node_ref") or event_payload.get("from_node_ref"))
    if parent_ref:
        parent_id = _resolve_node_id(conn, session_id, parent_ref)
        if parent_id is not None:
            conn.execute(
                """
                INSERT INTO edges (from_node_id, to_node_id, type)
                VALUES (?, ?, 'leads_to')
                """,
                (parent_id, node_id),
            )

    return node_id


def _apply_choice_selected(conn: sqlite3.Connection, session_id: int, event: EventIn) -> int:
    event_payload = event.payload
    node_ref = _clean_ref(event_payload.get("question_node_ref") or event_payload.get("node_ref"))

    node_id = (
        _resolve_node_id(conn, session_id, node_ref)
        if node_ref is not None
        else _latest_question_node_id(conn, session_id)
    )
    if node_id is None:
        raise HTTPException(status_code=404, detail="Target question node not found")

    choice_label = str(event_payload.get("choice_label", "")).strip()
    if not choice_label:
        raise HTTPException(status_code=400, detail="choice_selected requires payload.choice_label")

    choice_text = _clean_ref(event_payload.get("choice_text"))

    conn.execute(
        "UPDATE choices SET is_chosen = 0, chosen_at = NULL WHERE node_id = ?",
        (node_id,),
    )
    updated = conn.execute(
        """
        UPDATE choices
        SET is_chosen = 1, chosen_at = datetime('now')
        WHERE node_id = ? AND label = ?
        """,
        (node_id, choice_label),
    )
    if updated.rowcount == 0:
        conn.execute(
            """
            INSERT INTO choices (node_id, label, text, is_chosen, chosen_at)
            VALUES (?, ?, ?, 1, datetime('now'))
            """,
            (node_id, choice_label, choice_text or choice_label),
        )

    return node_id


def _apply_note_added(conn: sqlite3.Connection, session_id: int, event: EventIn) -> int:
    event_payload = event.payload
    node_ref = _clean_ref(event_payload.get("node_ref"))
    node_id = (
        _resolve_node_id(conn, session_id, node_ref)
        if node_ref is not None
        else _latest_question_node_id(conn, session_id)
    )
    if node_id is None:
        raise HTTPException(status_code=404, detail="Target node not found")

    note = str(event_payload.get("note", "")).strip()
    if not note:
        raise HTTPException(status_code=400, detail="note_added requires payload.note")

    row = conn.execute("SELECT rationale FROM nodes WHERE id = ?", (node_id,)).fetchone()
    existing = row["rationale"] if row else None
    merged = f"{existing}\n{note}" if existing else note
    conn.execute(
        "UPDATE nodes SET rationale = ?, updated_at = datetime('now') WHERE id = ?",
        (merged, node_id),
    )

    return node_id


def _apply_node_status_changed(
    conn: sqlite3.Connection, session_id: int, event: EventIn
) -> int:
    event_payload = event.payload
    node_ref = _clean_ref(event_payload.get("node_ref"))
    node_id = (
        _resolve_node_id(conn, session_id, node_ref)
        if node_ref is not None
        else _latest_question_node_id(conn, session_id)
    )
    if node_id is None:
        raise HTTPException(status_code=404, detail="Target node not found")

    new_status = str(event_payload.get("status", "")).strip()
    if new_status not in {"open", "in_progress", "blocked", "done"}:
        raise HTTPException(status_code=400, detail="Invalid status in node_status_changed")

    conn.execute(
        "UPDATE nodes SET status = ?, updated_at = datetime('now') WHERE id = ?",
        (new_status, node_id),
    )
    return node_id


def _latest_question_node_id(conn: sqlite3.Connection, session_id: int) -> int | None:
    row = conn.execute(
        """
        SELECT id
        FROM nodes
        WHERE session_id = ? AND type = 'question'
        ORDER BY id DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return int(row["id"])


def _resolve_node_id(
    conn: sqlite3.Connection, session_id: int, node_ref: str | None
) -> int | None:
    if not node_ref:
        return None

    if node_ref.isdigit():
        row = conn.execute(
            "SELECT id FROM nodes WHERE id = ? AND session_id = ?",
            (int(node_ref), session_id),
        ).fetchone()
        return int(row["id"]) if row else None

    row = conn.execute(
        "SELECT id FROM nodes WHERE external_ref = ? AND session_id = ?",
        (node_ref, session_id),
    ).fetchone()
    return int(row["id"]) if row else None


def _normalize_choice(item: Any, index: int) -> tuple[str, str]:
    if isinstance(item, dict):
        label = str(item.get("label") or chr(65 + index)).strip()
        text = str(item.get("text") or "").strip()
        return label, text
    return chr(65 + index), str(item).strip()


def _clean_ref(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None
