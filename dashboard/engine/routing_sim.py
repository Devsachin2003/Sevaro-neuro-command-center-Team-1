"""[5] Routing simulator: model (not replace) Sevaro's routing rules to predict
where the next calls go if a split-shift doctor joins.

The trap: a joiner starts at 0 consults, so rule 4 (fewest consults) sends them
nearly every call at every hospital they are credentialed for. Our fix: the joiner
is routable only at the gap hospitals named in the offer, and starts with a seeded
consult count equal to the on-shift median so they share gap calls instead of
absorbing all of them.
"""
import pandas as pd

LOW_VOLUME = {"Day": 6, "Float": 6, "Night": 4}
SIM_CALLS = 20
SIM_WINDOW_MIN = 90


def expected_calls(ref: dict, coverage: dict, n: int = SIM_CALLS) -> list[str]:
    """Deterministic call sequence for the next window, weighted by baseline mean
    (or the observed rate where a hospital is surging)."""
    s = coverage["surges"].set_index("hospital_id")
    w = {hid: max(r["mean"], r["last_60_min"] if r["surge"] else 0) for hid, r in s.iterrows()}
    total = sum(w.values())
    quota = {h: v / total * n for h, v in w.items()}
    alloc = {h: int(q) for h, q in quota.items()}
    for h in sorted(quota, key=lambda h: quota[h] - alloc[h], reverse=True)[: n - sum(alloc.values())]:
        alloc[h] += 1
    seq, remaining = [], dict(alloc)
    while any(remaining.values()):
        for h in sorted(remaining, key=lambda h: -alloc[h]):
            if remaining[h]:
                seq.append(h)
                remaining[h] -= 1
    return seq


def simulate(state: dict, ref: dict, coverage: dict, joiner: dict | None, restrict: bool) -> pd.DataFrame:
    """joiner: {physician_id, hospitals (gap hospitals), role}. Returns calls received per physician."""
    now, phys = state["now"], state["physicians"]
    creds = ref["credentials"].groupby("physician_id")["hospital_id"].apply(set).to_dict()
    mid = now + pd.Timedelta(minutes=SIM_WINDOW_MIN // 2)
    pool = {}
    for p in phys.itertuples():
        if p.derived_state == "Temporarily Unavailable" or p.shift_end <= mid:
            continue
        allowed = set(p.split_shift_hospitals) if isinstance(p.split_shift_hospitals, (list, set)) else creds.get(p.physician_id, set())
        pool[p.physician_id] = {"count": p.consults_this_shift, "last": p.last_answered,
                                "allowed": allowed, "limit": LOW_VOLUME["Day"], "got": []}
    if joiner:
        allowed = set(joiner["hospitals"]) if restrict else creds.get(joiner["physician_id"], set())
        seed = int(pd.Series([s["count"] for s in pool.values()]).median()) if restrict and pool else 0
        pool[joiner["physician_id"]] = {"count": seed, "seed": seed, "last": None, "allowed": allowed,
                                        "limit": LOW_VOLUME.get(joiner.get("role", "Day"), 6), "got": [],
                                        "joiner": True}

    unrouted = []
    for i, hid in enumerate(expected_calls(ref, coverage)):
        eligible = [pid for pid, s in pool.items() if hid in s["allowed"]]
        if not eligible:
            unrouted.append(hid)
            continue
        low = [pid for pid in eligible if pool[pid]["count"] <= pool[pid]["limit"]]
        cands = low or eligible
        oldest = pd.Timestamp.min.tz_localize("UTC")
        pick = min(cands, key=lambda pid: (pool[pid]["count"],
                                           pool[pid]["last"] if pool[pid]["last"] is not None and not pd.isna(pool[pid]["last"]) else oldest))
        pool[pick]["count"] += 1
        pool[pick]["last"] = now + pd.Timedelta(minutes=i)
        pool[pick]["got"].append(hid)

    rows = [{"physician_id": pid, "calls": len(s["got"]), "hospitals": sorted(set(s["got"])),
             "joiner": s.get("joiner", False), "seed": s.get("seed")} for pid, s in pool.items()]
    df = pd.DataFrame(rows).sort_values(["joiner", "calls"], ascending=False)
    df.attrs["unrouted"] = unrouted
    return df


def trap_comparison(state, ref, coverage, joiner) -> dict:
    before = simulate(state, ref, coverage, joiner, restrict=False)
    after = simulate(state, ref, coverage, joiner, restrict=True)
    j = joiner["physician_id"]
    jb, ja = before[before["physician_id"] == j].iloc[0], after[after["physician_id"] == j].iloc[0]
    return {"unrestricted": before, "restricted": after, "total_calls": SIM_CALLS,
            "joiner_unrestricted": int(jb["calls"]), "joiner_unrestricted_hospitals": jb["hospitals"],
            "joiner_restricted": int(ja["calls"]), "joiner_restricted_hospitals": ja["hospitals"],
            "seed": int(ja["seed"] or 0), "unrouted_restricted": after.attrs["unrouted"]}
