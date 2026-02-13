from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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


class SessionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class SessionOut(BaseModel):
    id: int
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


class NodeUpdate(BaseModel):
    status: NodeStatus | None = None
    rationale: str | None = None
    owner: str | None = None
    priority: int | None = None


class NodeOut(BaseModel):
    id: int
    session_id: int
    type: NodeType
    title: str
    status: NodeStatus
    rationale: str | None
    owner: str | None
    priority: int | None
    context_prompt: str | None
    created_at: str
    updated_at: str


def _rows_to_sessions(rows: list) -> list[SessionOut]:
    return [
        SessionOut(
            id=row["id"],
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
            SELECT id, name, started_at, ended_at, created_at
            FROM sessions
            ORDER BY datetime(created_at) DESC, id DESC
            """
        ).fetchall()
    return _rows_to_sessions(rows)


@app.post("/api/sessions", response_model=SessionOut, status_code=201)
def create_session(payload: SessionCreate) -> SessionOut:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO sessions (name, started_at)
            VALUES (?, ?)
            """,
            (payload.name.strip(), now),
        )
        session_id = cursor.lastrowid
        row = conn.execute(
            """
            SELECT id, name, started_at, ended_at, created_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    return _rows_to_sessions([row])[0]


@app.get("/api/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int) -> SessionOut:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, name, started_at, ended_at, created_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return _rows_to_sessions([row])[0]


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
                session_id, type, title, status, rationale, owner, priority, context_prompt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        node_id = cursor.lastrowid
        row = conn.execute(
            """
            SELECT
                id, session_id, type, title, status,
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
                id, session_id, type, title, status,
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
                id, session_id, type, title, status,
                rationale, owner, priority, context_prompt,
                created_at, updated_at
            FROM nodes
            WHERE id = ?
            """,
            (node_id,),
        ).fetchone()
    return _row_to_node(row)
