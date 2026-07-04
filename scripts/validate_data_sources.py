from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND = PROJECT_ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from worldcup_predictor.service import WorldCupService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate configured data sources.")
    parser.add_argument("--source", default="all", help="Source name to validate, e.g. sportmonks.")
    parser.add_argument("--db-path", default="data/worldcup.sqlite3", help="SQLite database path.")
    args = parser.parse_args()

    service = WorldCupService(db_path=args.db_path)
    payload = service.validate_data_sources()
    source = args.source.lower()
    rows = payload.get("sources", [])
    if source != "all":
        rows = [row for row in rows if source in str(row.get("name") or "").lower()]

    for row in rows:
        name = row.get("name")
        configured = "OK" if row.get("configured") else "MISSING"
        reachable = "OK" if row.get("reachable") else "SKIPPED"
        print(f"[INFO] {name} API key: {configured}")
        print(f"[INFO] {name} API connection: {reachable}")
        capabilities = row.get("capabilities") or {}
        if isinstance(capabilities, dict):
            for capability, status in capabilities.items():
                print(f"[INFO] {name} {capability} fetch: {status}")
        print(f"[INFO] {name} normalized records saved: {'OK' if row.get('sample_count', 0) else 'SKIPPED'}")
        if row.get("last_error"):
            print(f"[WARNING] {name}: {row['last_error']}")
        for warning in row.get("warnings") or []:
            print(f"[WARNING] {name}: {warning}")

    print(json.dumps({"sources": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
