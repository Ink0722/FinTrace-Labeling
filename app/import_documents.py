from __future__ import annotations

import argparse
from pathlib import Path

from .db import init_db
from .chunks import import_document_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Import documents.jsonl into SQLite.")
    parser.add_argument("--file", required=True, help="Path to documents jsonl file.")
    args = parser.parse_args()

    init_db()
    count = import_document_file(Path(args.file))
    print(f"Imported {count} documents")


if __name__ == "__main__":
    main()
