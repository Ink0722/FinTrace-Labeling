from __future__ import annotations

import json
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import ROOT, connect, export_jsonl, init_db, row_to_case, utc_now, validate_annotation
from .chunks import build_embedding_display, chunk_row_to_dict, document_row_to_dict, search_chunks


STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="FinTrace Labeling", lifespan=lifespan)


class AnnotationPayload(BaseModel):
    annotation_status: str = "pending"
    answerability: str | None = None
    required_entities: list[str] | str = []
    required_date: str | None = None
    valid_tools: list[str] | str = []
    required_chunk_ids: list[str] | str = []
    chunk_version: str | None = None
    annotator: str | None = None
    notes: str | None = None


class AddCaseChunkPayload(BaseModel):
    annotator: str | None = None


@app.get("/api/stats")
def stats() -> dict:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM annotations").fetchone()["n"]
        by_status = {
            row["annotation_status"]: row["n"]
            for row in conn.execute(
                "SELECT annotation_status, COUNT(*) AS n FROM annotations GROUP BY annotation_status"
            )
        }
        sessions = conn.execute("SELECT COUNT(DISTINCT session_id) AS n FROM annotations").fetchone()["n"]
    return {"total": total, "sessions": sessions, "by_status": by_status}


@app.get("/api/sessions")
def sessions() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                session_id,
                COUNT(*) AS total,
                SUM(annotation_status = 'pending') AS pending,
                SUM(annotation_status = 'completed') AS completed,
                SUM(annotation_status = 'review_required') AS review_required
            FROM annotations
            GROUP BY session_id
            ORDER BY CAST(session_id AS INTEGER)
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/cases")
def cases(
    session_id: str | None = None,
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
) -> list[dict]:
    clauses = []
    params = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if status:
        clauses.append("annotation_status = ?")
        params.append(status)
    if q:
        clauses.append("(question LIKE ? OR case_id LIKE ? OR annotator LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT * FROM annotations
        {where}
        ORDER BY CAST(session_id AS INTEGER), turn_id
    """
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row_to_case(row) for row in rows]


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str) -> dict:
    with connect() as conn:
        row = conn.execute("SELECT * FROM annotations WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Case not found")
        previous = conn.execute(
            """
            SELECT *
            FROM annotations
            WHERE session_id = ? AND turn_id < ?
            ORDER BY turn_id
            """,
            (row["session_id"], row["turn_id"]),
        ).fetchall()
    return {"case": row_to_case(row), "previous": [row_to_case(r) for r in previous]}


@app.put("/api/cases/{case_id}/annotation")
def save_annotation(case_id: str, payload: AnnotationPayload) -> dict:
    data = payload.dict()
    try:
        annotation = validate_annotation(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    now = utc_now()
    with connect() as conn:
        row = conn.execute("SELECT * FROM annotations WHERE case_id = ?", (case_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Case not found")
        conn.execute(
            """
            INSERT INTO annotation_history (case_id, annotator, snapshot_json, changed_at)
            VALUES (?, ?, ?, ?)
            """,
            (case_id, annotation["annotator"], json.dumps(row_to_case(row), ensure_ascii=False), now),
        )
        conn.execute(
            """
            UPDATE annotations
            SET annotation_status = ?,
                answerability = ?,
                required_entities = ?,
                required_date = ?,
                valid_tools = ?,
                required_chunk_ids = ?,
                chunk_version = ?,
                annotator = ?,
                notes = ?,
                updated_at = ?
            WHERE case_id = ?
            """,
            (
                annotation["annotation_status"],
                annotation["answerability"],
                json.dumps(annotation["required_entities"], ensure_ascii=False),
                annotation["required_date"],
                json.dumps(annotation["valid_tools"], ensure_ascii=False),
                json.dumps(annotation["required_chunk_ids"], ensure_ascii=False),
                annotation["chunk_version"],
                annotation["annotator"],
                annotation["notes"],
                now,
                case_id,
            ),
        )
        saved = conn.execute("SELECT * FROM annotations WHERE case_id = ?", (case_id,)).fetchone()
    export_jsonl()
    return row_to_case(saved)


@app.get("/api/chunk-versions")
def chunk_versions() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT version_id, name, source_file, imported_at, chunk_count, is_active
            FROM chunk_versions
            ORDER BY imported_at DESC
            """
        ).fetchall()
    return [{**dict(row), "is_active": bool(row["is_active"])} for row in rows]


@app.post("/api/chunk-versions/{version_id}/activate")
def activate_chunk_version(version_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM chunk_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Chunk version not found")
        conn.execute("UPDATE chunk_versions SET is_active = 0")
        conn.execute("UPDATE chunk_versions SET is_active = 1 WHERE version_id = ?", (version_id,))
    return {"version_id": version_id, "is_active": True}


@app.get("/api/chunks")
def chunks(
    q: str | None = Query(default=None),
    company_id: str | None = Query(default=None),
    version_id: str | None = Query(default=None),
    page: int = Query(default=1),
    page_size: int = Query(default=20),
) -> dict:
    try:
        return search_chunks(q, version_id, page, page_size, company_id=company_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/chunks/{version_id}/{chunk_id}")
def chunk_detail(version_id: str, chunk_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM chunks WHERE version_id = ? AND chunk_id = ?",
            (version_id, chunk_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Chunk not found")
        document = conn.execute(
            "SELECT * FROM documents WHERE document_id = ?", (row["document_id"],)
        ).fetchone()
    chunk = chunk_row_to_dict(row, include_text=True)
    doc = document_row_to_dict(document)
    chunk["document"] = doc
    chunk["embedding_display"] = build_embedding_display(chunk, doc)
    return chunk


@app.get("/api/documents/{version_id}/{document_id}/chunks")
def document_chunks(version_id: str, document_id: str) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM chunks
            WHERE version_id = ? AND document_id = ?
            ORDER BY chunk_index
            """,
            (version_id, document_id),
        ).fetchall()
    return [chunk_row_to_dict(row, include_text=False) for row in rows]


@app.post("/api/cases/{case_id}/chunks/{version_id}/{chunk_id}")
def add_case_chunk(case_id: str, version_id: str, chunk_id: str, payload: AddCaseChunkPayload | None = None) -> dict:
    now = utc_now()
    annotator = (payload.annotator if payload else None)
    annotator = annotator.strip() if annotator else None
    if not annotator:
        raise HTTPException(status_code=400, detail="Annotator is required")
    with connect() as conn:
        case = conn.execute("SELECT * FROM annotations WHERE case_id = ?", (case_id,)).fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        chunk = conn.execute(
            "SELECT 1 FROM chunks WHERE version_id = ? AND chunk_id = ?",
            (version_id, chunk_id),
        ).fetchone()
        if chunk is None:
            raise HTTPException(status_code=404, detail="Chunk not found")

        current_version = case["chunk_version"]
        ids = json.loads(case["required_chunk_ids"])
        if ids and current_version and current_version != version_id:
            raise HTTPException(
                status_code=400,
                detail=f"Case already uses chunk version {current_version}",
            )
        if chunk_id not in ids:
            ids.append(chunk_id)
        conn.execute(
            """
            UPDATE annotations
            SET required_chunk_ids = ?, chunk_version = ?, annotator = ?, updated_at = ?
            WHERE case_id = ?
            """,
            (json.dumps(ids, ensure_ascii=False), version_id, annotator, now, case_id),
        )
        saved = conn.execute("SELECT * FROM annotations WHERE case_id = ?", (case_id,)).fetchone()
    export_jsonl()
    return row_to_case(saved)


@app.delete("/api/cases/{case_id}/chunks/{chunk_id}")
def remove_case_chunk(case_id: str, chunk_id: str) -> dict:
    now = utc_now()
    with connect() as conn:
        case = conn.execute("SELECT * FROM annotations WHERE case_id = ?", (case_id,)).fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="Case not found")
        ids = [item for item in json.loads(case["required_chunk_ids"]) if item != chunk_id]
        next_version = case["chunk_version"] if ids else None
        conn.execute(
            """
            UPDATE annotations
            SET required_chunk_ids = ?, chunk_version = ?, updated_at = ?
            WHERE case_id = ?
            """,
            (json.dumps(ids, ensure_ascii=False), next_version, now, case_id),
        )
        saved = conn.execute("SELECT * FROM annotations WHERE case_id = ?", (case_id,)).fetchone()
    export_jsonl()
    return row_to_case(saved)


@app.post("/api/export/jsonl")
def export_annotations() -> dict:
    path = export_jsonl()
    return {"path": str(path.relative_to(ROOT))}


@app.get("/api/export/jsonl")
def download_annotations() -> FileResponse:
    path = export_jsonl()
    return FileResponse(path, filename=path.name, media_type="application/x-ndjson")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
