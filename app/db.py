from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = ROOT / "data" / "source" / "questions.jsonl"
DB_PATH = ROOT / "data" / "annotations.sqlite3"
EXPORT_PATH = ROOT / "evaluation" / "annotations" / "questions_annotated_v1.jsonl"

ANNOTATION_STATUSES = {"pending", "completed", "review_required"}
ANSWERABILITIES = {"answerable", "clarification_required", "unanswerable"}
TOOLS = {
    "financial_risk_analysis",
    "ownership_penetration",
    "document_search",
    "event_timeline",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS annotations (
                case_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                think_flag INTEGER NOT NULL,
                annotation_status TEXT NOT NULL DEFAULT 'pending',
                answerability TEXT,
                required_entities TEXT NOT NULL DEFAULT '[]',
                required_date TEXT,
                valid_tools TEXT NOT NULL DEFAULT '[]',
                required_chunk_ids TEXT NOT NULL DEFAULT '[]',
                annotator TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS annotation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                case_id TEXT NOT NULL,
                annotator TEXT,
                snapshot_json TEXT NOT NULL,
                changed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_annotations_session_turn ON annotations(session_id, turn_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_annotations_status ON annotations(annotation_status)"
        )
        ensure_column(conn, "annotations", "chunk_version", "TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunk_versions (
                version_id TEXT PRIMARY KEY,
                name TEXT,
                source_file TEXT,
                imported_at TEXT NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                version_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                document_id TEXT,
                chunk_index INTEGER,
                section_title TEXT,
                char_start INTEGER,
                text TEXT NOT NULL,
                PRIMARY KEY (version_id, chunk_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(version_id, document_id, chunk_index)"
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                document_type TEXT,
                company_id TEXT,
                title TEXT,
                published_date TEXT,
                publisher TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                text TEXT NOT NULL,
                source_ref TEXT
            )
            """
        )
        ensure_column(conn, "documents", "publisher", "TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_company ON documents(company_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_date ON documents(published_date)"
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
                USING fts5(version_id, chunk_id, document_id, section_title, text)
                """
            )
        except sqlite3.OperationalError:
            pass
    import_questions()


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def import_questions() -> None:
    if not QUESTIONS_PATH.exists():
        raise FileNotFoundError(f"Missing source data: {QUESTIONS_PATH}")

    session_counts: dict[str, int] = {}
    now = utc_now()
    with connect() as conn, QUESTIONS_PATH.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            session_id = str(item["session_id"])
            session_counts[session_id] = session_counts.get(session_id, 0) + 1
            turn_id = session_counts[session_id]
            case_id = f"SESSION-{int(session_id):03d}-TURN-{turn_id:03d}"
            question = item["question"]
            if not isinstance(question, str):
                question = str(question)
            conn.execute(
                """
                INSERT OR IGNORE INTO annotations (
                    case_id, session_id, turn_id, question, think_flag, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    session_id,
                    turn_id,
                    question,
                    1 if bool(item.get("think_flag")) else 0,
                    now,
                    now,
                ),
            )


def row_to_case(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "case_id": row["case_id"],
        "session_id": row["session_id"],
        "turn_id": row["turn_id"],
        "question": row["question"],
        "think_flag": bool(row["think_flag"]),
        "annotation_status": row["annotation_status"],
        "answerability": row["answerability"],
        "required_entities": json.loads(row["required_entities"]),
        "required_date": row["required_date"],
        "valid_tools": json.loads(row["valid_tools"]),
        "required_chunk_ids": json.loads(row["required_chunk_ids"]),
        "chunk_version": row["chunk_version"] if "chunk_version" in row.keys() else None,
        "annotator": row["annotator"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def validate_annotation(payload: dict[str, Any]) -> dict[str, Any]:
    status = payload.get("annotation_status", "pending")
    if status not in ANNOTATION_STATUSES:
        raise ValueError("annotation_status must be pending, completed, or review_required")

    answerability = payload.get("answerability")
    if answerability in ("", None):
        answerability = None
    elif answerability not in ANSWERABILITIES:
        raise ValueError("answerability is invalid")

    valid_tools = normalize_list(payload.get("valid_tools", []), "valid_tools")
    invalid_tools = [tool for tool in valid_tools if tool not in TOOLS]
    if invalid_tools:
        raise ValueError(f"Unknown tool(s): {', '.join(invalid_tools)}")

    required_date = payload.get("required_date")
    if required_date == "":
        required_date = None
    elif required_date is not None:
        try:
            datetime.strptime(str(required_date), "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("required_date must use YYYY-MM-DD") from exc

    return {
        "annotation_status": status,
        "answerability": answerability,
        "required_entities": normalize_list(payload.get("required_entities", []), "required_entities"),
        "required_date": required_date,
        "valid_tools": valid_tools,
        "required_chunk_ids": normalize_list(payload.get("required_chunk_ids", []), "required_chunk_ids"),
        "chunk_version": (payload.get("chunk_version") or "").strip() or None,
        "annotator": (payload.get("annotator") or "").strip() or None,
        "notes": (payload.get("notes") or "").strip() or None,
    }


def normalize_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.splitlines() if part.strip()]
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    normalized = []
    for item in value:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def export_jsonl() -> Path:
    EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn, EXPORT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        rows = conn.execute(
            "SELECT * FROM annotations ORDER BY CAST(session_id AS INTEGER), turn_id"
        ).fetchall()
        for row in rows:
            item = row_to_case(row)
            export_item = {
                "case_id": item["case_id"],
                "session_id": item["session_id"],
                "turn_id": item["turn_id"],
                "question": item["question"],
                "think_flag": item["think_flag"],
                "annotation_status": item["annotation_status"],
                "answerability": item["answerability"],
                "required_entities": item["required_entities"],
                "required_date": item["required_date"],
                "valid_tools": item["valid_tools"],
                "required_chunk_ids": item["required_chunk_ids"],
                "chunk_version": item["chunk_version"],
            }
            fh.write(json.dumps(export_item, ensure_ascii=False) + "\n")
    return EXPORT_PATH
