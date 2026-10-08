"""Optional Kaggle download helpers (requires `pip install .[kaggle]` and
Kaggle credentials via KAGGLE_USERNAME/KAGGLE_KEY or ~/.kaggle/kaggle.json)."""

from __future__ import annotations

import shutil
from pathlib import Path

DATASETS = {
    "kaggle-downtime": "agungpambudi/predict-manufacturing-downtime-performance-dataset",
    "factory-oee": "dubltap/factory-oee-and-downtime-synthetic-starter-dataset",
}

AI4I_URL = "https://archive.ics.uci.edu/static/public/601/ai4i+2020+predictive+maintenance+dataset.zip"


def download_kaggle(name: str, dest: Path) -> Path:
    try:
        import kagglehub
    except ImportError as e:
        raise RuntimeError("kagglehub not installed — run: pip install .[kaggle]") from e
    path = Path(kagglehub.dataset_download(DATASETS[name]))
    dest.mkdir(parents=True, exist_ok=True)
    for f in path.rglob("*.csv"):
        shutil.copy(f, dest / f.name)
    return dest


def download_ai4i(dest: Path) -> Path:
    """Direct download from UCI — no credentials needed."""
    import io
    import urllib.request
    import zipfile

    dest.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(AI4I_URL) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.filename.endswith(".csv"):
                zf.extract(info, dest)
    csvs = list(dest.rglob("*.csv"))
    if not csvs:
        raise RuntimeError("No CSV found in AI4I archive")
    return csvs[0]
