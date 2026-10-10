"""[7] Pipeline: one stateless call that turns events into problems and fixes.
Loading new data = calling analyze() again; nothing needs a restart."""
import pandas as pd

from .coverage import project
from .decide import eligibility, gap_hospitals, recommend
from .detect import detect
from .replay import replay
from .routing_sim import trap_comparison


def analyze(events: pd.DataFrame, ref: dict, now: pd.Timestamp,
            extra_shifts: list[dict] | None = None, offer_counts: dict | None = None) -> dict:
    extra_shifts = extra_shifts or []
    state = replay(events, ref, now, extra_shifts)
    cov = project(state, ref, extra_shifts)
    problems = detect(state, ref, cov)
    covered = {h for s in extra_shifts for h in s["hospitals"]}
    gaps = [h for h in gap_hospitals(problems, cov) if h not in covered]

    joined = {s["physician_id"] for s in extra_shifts}
    elig = eligibility(ref, now, gaps, offer_counts or {})
    elig = elig[~elig["physician_id"].isin(joined)].reset_index(drop=True)
    for p in problems:
        p["rec"] = recommend(p, state, ref, elig)

    # Split-shift trap: show it for the doctor who joined, or for the top candidate.
    roles = ref["off_shift"].set_index("physician_id")["role"].to_dict()
    if extra_shifts:
        s = extra_shifts[-1]
        joiner = {"physician_id": s["physician_id"], "hospitals": s["hospitals"],
                  "role": roles.get(s["physician_id"], "Day")}
    elif elig["eligible"].any():
        top = elig[elig["eligible"]].iloc[0]
        joiner = {"physician_id": top["physician_id"], "hospitals": top["covers"], "role": top["role"]}
    else:
        joiner = None
    trap = trap_comparison(state, ref, cov, joiner) if joiner else None

    return {"now": now, "state": state, "coverage": cov, "problems": problems,
            "gap_hospitals": gaps, "eligibility": elig, "trap": trap, "joiner": joiner}
