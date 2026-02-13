# Atrium

Atrium is a FastAPI + HTMX app that helps agentic developers track decision points from coding-agent sessions in a visual graph.

## Run locally

```bash
pip install -r requirements.txt
uvicorn interfaces.http.main:app --reload
```

Open:
- UI: `http://127.0.0.1:8000/sessions`
- API docs: `http://127.0.0.1:8000/docs`

## Test

```bash
pytest -q
```

## MCP ingest contract

Endpoint:
- `POST /api/events`

Top-level payload:
- `source` (string, optional, defaults to `mcp`)
- `event_type` (`question_presented | choice_selected | note_added | node_status_changed`)
- `session_external_id` (string, required)
- `agent_name` (string, optional)
- `timestamp` (string, optional)
- `payload` (object)

### `question_presented` payload
- `title` (required)
- `choices` (array; either string entries or `{label,text}` objects)
- `node_ref` (optional external node reference)
- `parent_node_ref` or `from_node_ref` (optional parent link)
- `context_prompt` (optional)
- `rationale` (optional)

### `choice_selected` payload
- `choice_label` (required)
- `question_node_ref` or `node_ref` (optional; falls back to latest question node)
- `choice_text` (optional if label does not exist yet)

### `note_added` payload
- `note` (required)
- `node_ref` (optional; falls back to latest question node)

### `node_status_changed` payload
- `status` (`open | in_progress | blocked | done`)
- `node_ref` (optional; falls back to latest question node)

## Sample fixture payloads

Fixture files live in:
- `tests/fixtures/mcp_events/session_happy_path.json`
- `tests/fixtures/mcp_events/session_blocked_branch.json`

These files are used by automated tests and can also be reused by MCP clients as reference payloads.
