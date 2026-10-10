"""[3] Coverage projector: credentialed capacity per hospital from now to now + horizon.

A hospital is short when fewer credentialed physicians than required will be on
shift (projected) or free right now (current). A demand surge (trailing 60-min
consults above the hospital's p90 for this hour) raises the requirement by one.

Assumptions (see DECISIONS.md):
  * scheduled shift starts from the off-shift roster count from their start time (N4)
  * a surge detected now is assumed to persist across the horizon (conservative)
  * a Temporarily Unavailable physician is not counted until their status changes
  * an accepted split shift counts only at its restricted gap hospitals (trap fix)
"""
import pandas as pd

from .replay import shift_window

HORIZON_MIN = 120
NIGHT_SHIFT_HOURS = 12


def _block_contains(block: str, t: pd.Timestamp) -> bool:
    start, end = block.split("-")
    m = t.hour * 60 + t.minute
    s = int(start[:2]) * 60 + int(start[3:])
    e = int(end[:2]) * 60 + int(end[3:])
    return s <= m < e if s < e else (m >= s or m < e)


def required_at(ref: dict, hospital_id: str, t: pd.Timestamp) -> int:
    rows = ref["coverage"][ref["coverage"]["hospital_id"] == hospital_id]
    for r in rows.itertuples():
        if _block_contains(r.time_block_et, t):
            return int(r.min_available_credentialed_physicians)
    return 1


def detect_surges(events: pd.DataFrame, ref: dict, now: pd.Timestamp) -> pd.DataFrame:
    recv = events[(events["event_type"] == "consult_received")
                  & (events["event_time"] > now - pd.Timedelta(minutes=60))
                  & (events["event_time"] <= now)]
    counts = recv.groupby("hospital_id")["consult_id"].nunique()
    hour = f"{now.hour:02d}:00"
    base = ref["baseline"][ref["baseline"]["hour_start_et"] == hour].set_index("hospital_id")
    rows = []
    for hid in ref["hospitals"]["hospital_id"]:
        n = int(counts.get(hid, 0))
        p90 = float(base.loc[hid, "p90_consults"]) if hid in base.index else float("inf")
        mean = float(base.loc[hid, "mean_consults"]) if hid in base.index else 0.0
        rows.append({"hospital_id": hid, "last_60_min": n, "p90": p90, "mean": mean, "surge": n > p90})
    return pd.DataFrame(rows)


def checkpoints(now: pd.Timestamp, horizon_min: int = HORIZON_MIN) -> list[pd.Timestamp]:
    end = now + pd.Timedelta(minutes=horizon_min)
    pts = {now}
    t = now.floor("30min") + pd.Timedelta(minutes=30)
    while t <= end:
        pts.add(t)
        t += pd.Timedelta(minutes=30)
    return sorted(pts)


def shift_roster(ref: dict, now: pd.Timestamp, extra_shifts: list[dict]) -> list[dict]:
    """Every shift that could be live inside the horizon, with the hospitals it may cover."""
    creds = ref["credentials"].groupby("physician_id")["hospital_id"].apply(set).to_dict()
    extra_ids = {s["physician_id"] for s in extra_shifts}
    shifts = []
    for pid in ref["on_shift"]["physician_id"]:
        if pid in extra_ids:
            continue
        start, end = shift_window(pid, ref, now)
        shifts.append({"physician_id": pid, "start": start, "end": end,
                       "hospitals": creds.get(pid, set()), "kind": "on shift"})
    for r in ref["off_shift"].itertuples():
        if r.physician_id in extra_ids:
            continue
        start = r.next_shift_start
        if now - pd.Timedelta(hours=NIGHT_SHIFT_HOURS) < start <= now + pd.Timedelta(minutes=HORIZON_MIN):
            shifts.append({"physician_id": r.physician_id, "start": start,
                           "end": start + pd.Timedelta(hours=NIGHT_SHIFT_HOURS),
                           "hospitals": creds.get(r.physician_id, set()), "kind": "scheduled start"})
    for s in extra_shifts:
        shifts.append({**s, "hospitals": set(s["hospitals"]), "kind": "split shift"})
    return shifts


def project(state: dict, ref: dict, extra_shifts: list[dict] | None = None) -> dict:
    now, phys = state["now"], state["physicians"]
    extra_shifts = extra_shifts or []
    surges = detect_surges(state["events"], ref, now).set_index("hospital_id")
    shifts = shift_roster(ref, now, extra_shifts)
    unavailable = set(phys.loc[phys["derived_state"] == "Temporarily Unavailable", "physician_id"])
    free_now = set(phys.loc[phys["available_for_routing"], "physician_id"])
    names = ref["hospitals"].set_index("hospital_id")["hospital_name"]

    rows = []
    for hid in ref["hospitals"]["hospital_id"]:
        surge = bool(surges.loc[hid, "surge"])
        for t in checkpoints(now):
            staffed = sorted(s["physician_id"] for s in shifts
                             if s["start"] <= t < s["end"] and hid in s["hospitals"]
                             and s["physician_id"] not in unavailable)
            required = required_at(ref, hid, t) + (1 if surge else 0)
            row = {"hospital_id": hid, "hospital_name": names[hid], "time": t,
                   "required": required, "staffed": len(staffed), "staffed_ids": staffed,
                   "surge": surge, "gap": len(staffed) < required}
            if t == now:
                free = [p for p in staffed if p in free_now]
                row.update(free_now=len(free), free_ids=free, short_now=len(free) < required)
            rows.append(row)
    grid = pd.DataFrame(rows)

    # Per-hospital summary: credential depth and first projected gap.
    summary = []
    for hid, g in grid.groupby("hospital_id"):
        now_row = g[g["time"] == now].iloc[0]
        future_gaps = g[g["gap"]]
        summary.append({
            "hospital_id": hid, "hospital_name": names[hid],
            "credentialed_on_shift_now": now_row["staffed"],
            "free_now": now_row["free_now"], "required_now": now_row["required"],
            "min_staffed_in_window": int(g["staffed"].min()),
            "surge": bool(now_row["surge"]),
            "last_60_min": int(surges.loc[hid, "last_60_min"]), "p90": surges.loc[hid, "p90"],
            "first_gap": future_gaps["time"].min() if len(future_gaps) else None,
            "gap_shortfall": int((g["required"] - g["staffed"]).max()),
        })
    return {"grid": grid, "summary": pd.DataFrame(summary), "surges": surges.reset_index(),
            "shifts": shifts}
