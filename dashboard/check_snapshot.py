"""Replay the sample event stream to 18:20 ET and diff against the reference snapshots.

Run:  python check_snapshot.py
"""
import sys

import pandas as pd

from engine.loader import DATA_DIR, ET, load_events, load_reference
from engine.replay import replay

NOW = pd.Timestamp("2026-03-17 18:20:00", tz=ET)


def compare(name, ours: pd.DataFrame, ref: pd.DataFrame, key: str, cols: list[str]) -> int:
    merged = ref.merge(ours, on=key, how="outer", suffixes=("_ref", "_ours"), indicator=True)
    errors = 0
    for _, row in merged.iterrows():
        if row["_merge"] != "both":
            print(f"  [{name}] {row[key]}: only in {row['_merge']}")
            errors += 1
            continue
        for col in cols:
            a, b = str(row[f"{col}_ref"]), str(row[f"{col}_ours"])
            if a != b:
                print(f"  [{name}] {row[key]} {col}: expected {a!r}, got {b!r}")
                errors += 1
    print(f"{name}: {len(ref)} rows, {errors} mismatches")
    return errors


def main() -> int:
    state = replay(load_events(), load_reference(), NOW)

    consults = state["consults"].copy()
    consults["stage_entered_at"] = consults["stage_entered_at"].dt.strftime("%H:%M:%S")
    errors = compare("open consults", consults,
                     pd.read_csv(DATA_DIR / "snapshot_open_consults_1820.csv"), "consult_id",
                     ["hospital_id", "physician_id", "consult_type", "current_stage",
                      "stage_entered_at", "minutes_in_stage", "minutes_since_received"])
    errors += compare("physicians", state["physicians"],
                      pd.read_csv(DATA_DIR / "snapshot_physicians_1820.csv"), "physician_id",
                      ["shift_et", "self_reported_status", "desktop_agent", "derived_state",
                       "consults_this_shift", "calls_this_shift", "open_consults",
                       "minutes_to_shift_end"])

    q = state["quality"]
    print(f"\nduplicates removed: {list(q['duplicates']['event_id'])}")
    print(f"late events (>60s): {list(q['late']['event_id'])}")
    print("\nPASS" if errors == 0 else f"\nFAIL ({errors} mismatches)")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
