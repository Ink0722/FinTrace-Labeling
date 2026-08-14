from __future__ import annotations

import argparse
from pathlib import Path

from .db import init_db
from .chunks import import_chunk_file


def main() -> None:
    parser = argparse.ArgumentParser(description="Import versioned chunks.jsonl into SQLite.")
    parser.add_argument("--file", required=True, help="Path to chunks jsonl file.")
    parser.add_argument("--name", help="Human-readable version name.")
    parser.add_argument("--activate", action="store_true", help="Make this version the default search version.")
    args = parser.parse_args()

    init_db()
    version_id, count = import_chunk_file(Path(args.file), args.name, args.activate)
    print(f"Imported {count} chunks into version {version_id}")


if __name__ == "__main__":
    main()
