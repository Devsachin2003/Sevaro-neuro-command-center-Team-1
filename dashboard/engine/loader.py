"""Load the challenge CSVs. All times are normalised to US/Eastern."""
import os
from pathlib import Path

import pandas as pd

ET = "America/New_York"
# dashboard/data by default; set SEVARO_DATA_DIR to point at another data package.
DATA_DIR = Path(os.environ.get("SEVARO_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))

EVENT_COLUMNS = ["event_id", "event_time", "ingested_at", "consult_id",
                 "event_type", "physician_id", "hospital_id", "description"]


def parse_events(df: pd.DataFrame) -> pd.DataFrame:
    """Validate and type an event_stream-shaped frame (also used for uploaded files)."""
    missing = set(EVENT_COLUMNS) - set(df.columns)
    if missing:
        raise ValueError(f"Event file is missing columns: {sorted(missing)}")
    df = df[EVENT_COLUMNS].copy()
    for col in ("event_time", "ingested_at"):
        df[col] = pd.to_datetime(df[col], utc=True).dt.tz_convert(ET)
    for col in ("consult_id", "physician_id", "hospital_id", "description"):
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["event_type"] = df["event_type"].str.strip()
    return df


def load_events(path: Path | None = None) -> pd.DataFrame:
    return parse_events(pd.read_csv(path or DATA_DIR / "event_stream.csv"))


def load_reference(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    """Static reference files (hospitals, shifts, credentialing, baselines, requirements)."""
    ref = {
        "hospitals": pd.read_csv(data_dir / "hospitals.csv"),
        "on_shift": pd.read_csv(data_dir / "physicians_on_shift.csv"),
        "off_shift": pd.read_csv(data_dir / "physicians_off_shift_roster.csv"),
        "baseline": pd.read_csv(data_dir / "baseline_hourly_volume.csv"),
        "coverage": pd.read_csv(data_dir / "coverage_requirements.csv"),
    }
    cred = pd.read_csv(data_dir / "credentialing_matrix.csv")
    hosp_cols = [c for c in cred.columns if c.startswith("SYN-")]
    long = cred.melt(id_vars=["physician_id", "name"], value_vars=hosp_cols,
                     var_name="hospital_id", value_name="ok")
    ref["credentials"] = long[long["ok"].astype(str).str.upper() == "Y"][["physician_id", "hospital_id"]]
    for col in ("last_shift_end", "next_shift_start"):
        ref["off_shift"][col] = pd.to_datetime(ref["off_shift"][col], utc=True).dt.tz_convert(ET)
    return ref
