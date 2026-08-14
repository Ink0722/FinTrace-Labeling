from __future__ import annotations

from .db import connect, export_jsonl, utc_now


def main() -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute("DELETE FROM annotation_history")
        conn.execute(
            """
            UPDATE annotations
            SET annotation_status = 'pending',
                answerability = NULL,
                required_entities = '[]',
                required_date = NULL,
                valid_tools = '[]',
                required_chunk_ids = '[]',
                chunk_version = NULL,
                annotator = NULL,
                notes = NULL,
                updated_at = ?
            """,
            (now,),
        )
        conn.execute("UPDATE chunk_versions SET is_active = 0")
        conn.execute("UPDATE chunk_versions SET is_active = 1 WHERE version_id = 'chunks-v2'")

        total = conn.execute("SELECT COUNT(*) FROM annotations").fetchone()[0]
        history = conn.execute("SELECT COUNT(*) FROM annotation_history").fetchone()[0]
        active = conn.execute("SELECT version_id FROM chunk_versions WHERE is_active = 1").fetchone()

    export_jsonl()
    print(f"reset_at={now}")
    print(f"annotations={total}")
    print(f"history={history}")
    print(f"active_chunk_version={active[0] if active else 'NONE'}")


if __name__ == "__main__":
    main()
