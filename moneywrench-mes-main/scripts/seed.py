"""Create the database and load production history.

    python scripts/seed.py                      # synthetic 12 months (default)
    python scripts/seed.py --months 6
    python scripts/seed.py --source ai4i --download
    python scripts/seed.py --source kaggle-downtime --download  # needs Kaggle creds
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mes.db.models import Base
from mes.db.session import PROJECT_ROOT, get_engine, get_session, init_db

RAW = PROJECT_ROOT / "data" / "raw"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default="synthetic",
                    choices=["synthetic", "kaggle-downtime", "ai4i"])
    ap.add_argument("--months", type=int, default=12, help="synthetic history length")
    ap.add_argument("--download", action="store_true",
                    help="download the dataset first (ai4i: no auth; kaggle: needs creds)")
    ap.add_argument("--keep", action="store_true",
                    help="keep existing data instead of recreating the DB")
    args = ap.parse_args()

    engine = get_engine()
    if not args.keep:
        Base.metadata.drop_all(engine)
    init_db(engine)
    session = get_session(engine)

    if args.source == "synthetic":
        from mes.ingest.synthetic import generate
        counts = generate(session, months=args.months)
    elif args.source == "ai4i":
        from mes.ingest.adapters.ai4i import load
        folder = RAW / "ai4i"
        if args.download:
            from mes.ingest.kaggle import download_ai4i
            csv = download_ai4i(folder)
        else:
            csvs = list(folder.rglob("*.csv"))
            if not csvs:
                sys.exit(f"No CSV in {folder} — rerun with --download")
            csv = csvs[0]
        counts = load(session, csv)
    else:  # kaggle-downtime
        from mes.ingest.adapters.kaggle_downtime import load
        folder = RAW / "kaggle_downtime"
        if args.download:
            from mes.ingest.kaggle import download_kaggle
            download_kaggle("kaggle-downtime", folder)
        counts = load(session, folder)

    print(f"Loaded [{args.source}]: " +
          ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
