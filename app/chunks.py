from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .db import connect, utc_now


def has_fts(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'chunks_fts'"
    ).fetchone()
    return row is not None


def active_version(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT version_id FROM chunk_versions WHERE is_active = 1 ORDER BY imported_at DESC LIMIT 1"
    ).fetchone()
    if row:
        return row["version_id"]
    row = conn.execute(
        "SELECT version_id FROM chunk_versions ORDER BY imported_at DESC LIMIT 1"
    ).fetchone()
    return row["version_id"] if row else None


def import_chunk_file(path: Path, name: str | None = None, activate: bool = False) -> tuple[str, int]:
    if not path.exists():
        raise FileNotFoundError(path)

    now = utc_now()
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        first_line = next((line.strip() for line in fh if line.strip()), None)
    if first_line is None:
        raise ValueError("Chunk file is empty")
    first_item = json.loads(first_line)
    version_id = first_item.get("chunk_version")
    if not version_id:
        raise ValueError("Every chunk row must include chunk_version")
    version_id = str(version_id)

    with connect() as conn:
        exists = conn.execute(
            "SELECT 1 FROM chunk_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        if exists:
            raise ValueError(f"Chunk version already exists: {version_id}")

        if activate:
            conn.execute("UPDATE chunk_versions SET is_active = 0")
        conn.execute(
            """
            INSERT INTO chunk_versions (version_id, name, source_file, imported_at, chunk_count, is_active)
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            (version_id, name or version_id, str(path), now, 1 if activate else 0),
        )

        fts_enabled = has_fts(conn)
        with path.open("r", encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                row_version = item.get("chunk_version")
                if not row_version:
                    raise ValueError(f"Missing chunk_version at line {line_no}")
                if str(row_version) != version_id:
                    raise ValueError(
                        f"Inconsistent chunk_version at line {line_no}: {row_version} != {version_id}"
                    )
                values = (
                    version_id,
                    str(item["chunk_id"]),
                    item.get("document_id"),
                    item.get("chunk_index"),
                    item.get("section_title"),
                    item.get("char_start"),
                    item.get("text") or "",
                )
                conn.execute(
                    """
                    INSERT INTO chunks (
                        version_id, chunk_id, document_id, chunk_index, section_title, char_start, text
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                if fts_enabled:
                    conn.execute(
                        """
                        INSERT INTO chunks_fts (version_id, chunk_id, document_id, section_title, text)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (values[0], values[1], values[2], values[4], values[6]),
                    )
                count += 1

        conn.execute(
            "UPDATE chunk_versions SET chunk_count = ? WHERE version_id = ?",
            (count, version_id),
        )
    return version_id, count


def import_document_file(path: Path) -> int:
    if not path.exists():
        raise FileNotFoundError(path)

    count = 0
    with connect() as conn, path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            conn.execute(
                """
                INSERT OR REPLACE INTO documents (
                    document_id, document_type, company_id, title, published_date, publisher, tags, text, source_ref
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item["document_id"]),
                    item.get("document_type"),
                    item.get("company_id"),
                    item.get("title"),
                    item.get("published_date"),
                    item.get("publisher"),
                    json.dumps(item.get("tags") or [], ensure_ascii=False),
                    item.get("text") or "",
                    item.get("source_ref"),
                ),
            )
            count += 1
    return count


def chunk_row_to_dict(row: sqlite3.Row, include_text: bool = True) -> dict[str, Any]:
    item = {
        "version_id": row["version_id"],
        "chunk_id": row["chunk_id"],
        "document_id": row["document_id"],
        "chunk_index": row["chunk_index"],
        "section_title": row["section_title"],
        "char_start": row["char_start"],
    }
    text = row["text"]
    if include_text:
        item["text"] = text
    else:
        item["snippet"] = text[:260] + ("..." if len(text) > 260 else "")
    if "title" in row.keys():
        item["title"] = row["title"]
    if "company_id" in row.keys():
        item["company_id"] = row["company_id"]
    if "published_date" in row.keys():
        item["published_date"] = row["published_date"]
    if "tags" in row.keys() and row["tags"]:
        item["tags"] = json.loads(row["tags"])
    return item


def document_row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "document_id": row["document_id"],
        "document_type": row["document_type"],
        "company_id": row["company_id"],
        "title": row["title"],
        "published_date": row["published_date"],
        "publisher": row["publisher"] if "publisher" in row.keys() else None,
        "tags": json.loads(row["tags"]),
        "source_ref": row["source_ref"],
    }


def build_embedding_display(chunk: dict[str, Any], document: dict[str, Any] | None) -> str:
    parts: list[str] = []
    doc_type_labels = {
        "announcement": "公告",
        "research_report": "研报摘要",
        "research": "研报摘要",
        "report": "研报摘要",
    }
    if document:
        document_type = document.get("document_type")
        document_type_label = doc_type_labels.get(str(document_type), document_type)
        fields = [
            ("文档类型", document_type_label),
            ("证券代码", document.get("company_id")),
            ("标题", document.get("title")),
            ("发布日期", document.get("published_date")),
        ]
        if document_type_label == "研报摘要" and document.get("publisher"):
            fields.append(("发布机构", document.get("publisher")))
        tags = unique_nonempty(document.get("tags") or [])
        if tags:
            fields.append(("标签", "；".join(tags)))
        section_title = chunk.get("section_title")
        if section_title:
            fields.append(("章节", section_title))
        parts.extend(f"{name}：{value}" for name, value in fields if value)
    elif chunk.get("section_title"):
        parts.append(f"章节：{chunk.get('section_title')}")

    parts.append("")
    parts.append("正文：")
    parts.append(chunk.get("text") or "")
    return "\n".join(parts)


def unique_nonempty(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def search_chunks(
    q: str | None,
    version_id: str | None,
    page: int,
    page_size: int,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    offset = (page - 1) * page_size

    with connect() as conn:
        resolved_version = version_id or active_version(conn)
        if not resolved_version:
            return {"version_id": None, "items": [], "total": 0, "page": page, "page_size": page_size}

        if q:
            like = f"%{q}%"
            rows, total = like_search(conn, resolved_version, like, page_size, offset)
        else:
            rows = conn.execute(
                """
                SELECT * FROM chunks
                WHERE version_id = ?
                ORDER BY document_id, chunk_index
                LIMIT ? OFFSET ?
                """,
                (resolved_version, page_size, offset),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM chunks WHERE version_id = ?", (resolved_version,)
            ).fetchone()["n"]

    return {
        "version_id": resolved_version,
        "items": [chunk_row_to_dict(row, include_text=False) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def like_search(conn: sqlite3.Connection, version_id: str, like: str, limit: int, offset: int):
    clauses = """
        c.version_id = ? AND (
            c.chunk_id LIKE ?
            OR c.document_id LIKE ?
            OR c.section_title LIKE ?
            OR c.text LIKE ?
            OR d.document_type LIKE ?
            OR d.company_id LIKE ?
            OR d.title LIKE ?
            OR d.published_date LIKE ?
            OR d.publisher LIKE ?
            OR d.tags LIKE ?
        )
    """
    params = (version_id, like, like, like, like, like, like, like, like, like, like)
    rows = conn.execute(
        f"""
        SELECT c.*, d.company_id, d.title, d.published_date, d.tags
        FROM chunks c
        LEFT JOIN documents d ON d.document_id = c.document_id
        WHERE {clauses}
        ORDER BY c.document_id, c.chunk_index
        LIMIT ? OFFSET ?
        """,
        (*params, limit, offset),
    ).fetchall()
    total = conn.execute(
        f"""
        SELECT COUNT(*) AS n
        FROM chunks c
        LEFT JOIN documents d ON d.document_id = c.document_id
        WHERE {clauses}
        """,
        params,
    ).fetchone()["n"]
    return rows, total
