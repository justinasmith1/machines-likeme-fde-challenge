import argparse
import sys
from pathlib import Path

from app.config import get_settings
from app.db.session import init_db, make_engine, make_session_factory
from app.reference.loader import ReferenceDataError, load_reference


def _load_reference(directory: Path) -> int:
    engine = make_engine(get_settings().database_url)
    init_db(engine)
    session = make_session_factory(engine)()
    try:
        summary = load_reference(directory, session)
    except ReferenceDataError as exc:
        print(f"Reference load FAILED with {len(exc.errors)} error(s):", file=sys.stderr)
        for err in exc.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    finally:
        session.close()
    print("Reference data loaded:")
    for name, count in summary.counts.items():
        print(f"  {name}: {count} records (sha256 {summary.sha256[name][:12]}...)")
    print("Errors: none")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load-reference", help="Validate and load reference JSON files")
    load.add_argument("--dir", default=None, help="Directory with the three JSON files")
    args = parser.parse_args(argv)

    if args.command == "load-reference":
        return _load_reference(Path(args.dir or get_settings().reference_data_dir))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
