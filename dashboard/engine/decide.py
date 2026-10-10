"""[6] Decider: for each problem pick the intervention, who approves it, and why
that fix over the alternatives. Also builds the split-shift eligibility table:
every off-shift physician x every rule, pass/fail with a reason.
"""
import pandas as pd

OFFER_HOURS = 3.0
MIN_REST_H = 10
MAX_HOURS_7D = 60
MIN_OFFER_VALUE_H = 2
MAX_OFFERS_WEEK = 3

# RACI per intervention (mentor answers, see DECISIONS.md)
RACI = {
    "reassign":    {"R": "Coordinator", "A": "Coordinator (no approval needed)", "C": "—",
                    "I": "Original physician (after), Quality Leadership"},
    "split_shift": {"R": "Coordinator", "A": "Clinical lead", "C": "Ops Leadership",
                    "I": "Quality Leadership"},
    "escalate":    {"R": "Coordinator", "A": "Clinical lead", "C": "Ops Leadership",
                    "I": "Quality Leadership"},
    "alert":       {"R": "System (automatic)", "A": "—", "C": "—", "I": "Coordinator"},
}
ACTION_LABEL = {"reassign": "Reassign consult", "split_shift": "Offer split shift",
                "escalate": "Escalate to clinical lead", "alert": "Internal alert (automatic)"}


def offer_window(now: pd.Timestamp, hours: float = OFFER_HOURS):
    start = now.ceil("5min")
    return start, start + pd.Timedelta(hours=hours)


def gap_hospitals(problems: list[dict], coverage: dict) -> list[str]:
    """Hospitals a split shift should cover: projected gaps, plus hospitals where a
    consult is waiting because no credentialed doctor is free right now."""
    hosp = {p["hospital_id"] for p in problems if p["kind"] == "gap"}
    short_now = coverage["grid"].dropna(subset=["short_now"])
    short_now = set(short_now.loc[short_now["short_now"].astype(bool), "hospital_id"])
    hosp |= {p["hospital_id"] for p in problems
             if p["kind"] == "consult" and p.get("physician_id") is None and p["hospital_id"] in short_now}
    return sorted(hosp)


def eligibility(ref: dict, now: pd.Timestamp, gap_hosp: list[str], offer_counts: dict,
                hours: float = OFFER_HOURS) -> pd.DataFrame:
    """offer_counts: {physician_id: {"open": int, "week": int}} from the action store."""
    start, end = offer_window(now, hours)
    creds = ref["credentials"].groupby("physician_id")["hospital_id"].apply(set).to_dict()
    names = ref["hospitals"].set_index("hospital_id")["hospital_name"].to_dict()
    rows = []
    for r in ref["off_shift"].itertuples():
        pid = r.physician_id
        covers = sorted(creds.get(pid, set()) & set(gap_hosp))
        rest_h = (start - r.last_shift_end).total_seconds() / 3600
        value_h = hours - r.est_minutes_to_online / 60
        counts = {"open": 0, "week": 0, "declined_today": 0, **offer_counts.get(pid, {})}
        rules = {
            "Opted in": (str(r.split_shift_opt_in).lower() == "yes",
                         "Opted in" if str(r.split_shift_opt_in).lower() == "yes" else "Has not opted in to split shifts"),
            "Credentialed at gap": (bool(covers),
                                    "Covers " + ", ".join(names[h] for h in covers) if covers
                                    else "Not credentialed at any short hospital"),
            "10h rest": (rest_h >= MIN_REST_H, f"{rest_h:.1f} h since last shift (needs {MIN_REST_H})"),
            "≤60h in 7 days": (r.hours_worked_last_7d + hours <= MAX_HOURS_7D,
                               f"{r.hours_worked_last_7d} + {hours:g} = {r.hours_worked_last_7d + hours:g} h (max {MAX_HOURS_7D})"),
            "No shift clash": (r.next_shift_start >= end,
                               f"Next shift {r.next_shift_start:%a %H:%M}" + ("" if r.next_shift_start >= end
                                                                             else f", overlaps offer ending {end:%H:%M}")),
            "Worth ≥2h": (value_h >= MIN_OFFER_VALUE_H,
                          f"{hours:g} h − {r.est_minutes_to_online} min to get online = {value_h:.1f} h"),
            "Offer limits": (counts["open"] == 0 and counts["week"] < MAX_OFFERS_WEEK
                             and counts["declined_today"] == 0,
                             f"{counts['open']} open, {counts['week']}/{MAX_OFFERS_WEEK} this week"
                             + (", declined/expired today" if counts["declined_today"] else "")),
        }
        failed = [k for k, (ok, _) in rules.items() if not ok]
        rows.append({
            "physician_id": pid, "name": r.name, "role": r.role, "eligible": not failed, "offer_open": counts["open"] > 0,
            "failed_rules": failed, "rules": rules, "covers": covers,
            "minutes_to_online": r.est_minutes_to_online, "hours_7d": r.hours_worked_last_7d,
            "declined_30d": r.offers_declined_last_30d, "channel": r.preferred_channel,
            "offer_start": start, "offer_end": end, "online_at": start + pd.Timedelta(minutes=int(r.est_minutes_to_online)),
        })
    df = pd.DataFrame(rows)
    # Ranking among eligible (open question E1): most gap hospitals covered, fastest online,
    # fewest hours worked (fairness/fatigue), fewest recent declines.
    df["n_covers"] = df["covers"].apply(len)
    df = df.sort_values(["eligible", "n_covers", "minutes_to_online", "hours_7d", "declined_30d"],
                        ascending=[False, False, True, True, True]).reset_index(drop=True)
    df["rank"] = [i + 1 if e else None for i, e in enumerate(df["eligible"])]
    return df


def reassign_candidates(state: dict, ref: dict, hospital_id: str, exclude: str | None) -> pd.DataFrame:
    phys = state["physicians"]
    cred = set(ref["credentials"].loc[ref["credentials"]["hospital_id"] == hospital_id, "physician_id"])
    c = phys[phys["physician_id"].isin(cred) & phys["available_for_routing"]
             & (phys["physician_id"] != exclude) & (phys["minutes_to_shift_end"] > 30)]
    return c.sort_values(["open_consults", "consults_this_shift"])


def _why_not_busy(state, ref, hospital_id, exclude):
    phys = state["physicians"]
    cred = set(ref["credentials"].loc[ref["credentials"]["hospital_id"] == hospital_id, "physician_id"])
    rows = phys[phys["physician_id"].isin(cred) & (phys["physician_id"] != exclude)]
    return [f"{r.physician_id}: {r.derived_state}" for r in rows.itertuples()]


def recommend(problem: dict, state: dict, ref: dict, elig: pd.DataFrame) -> dict:
    kind = problem["kind"]
    pnames = pd.concat([ref["on_shift"], ref["off_shift"]]).set_index("physician_id")["name"].to_dict()
    eligible = elig[elig["eligible"]]
    # A doctor whose only "failure" is the offer already pending for this gap is still the plan.
    planned = elig[elig["eligible"] | (elig["offer_open"] & elig["failed_rules"].apply(lambda f: f == ["Offer limits"]))]
    rec = {"action": "alert", "target": None, "detail": "", "rationale": [], "why_not": []}

    def split_or_escalate(hid):
        top = planned[planned["covers"].apply(lambda c: hid in c)]
        if len(top):
            t = top.iloc[0]
            rec.update(action="split_shift", target=t["physician_id"],
                       detail=f"Offer {t['name']} {t['offer_start']:%H:%M}–{t['offer_end']:%H:%M} "
                              f"(online ~{t['online_at']:%H:%M}), limited to the short hospitals")
            rec["rationale"].append(f"{t['name']} is the top-ranked of {len(planned)} eligible off-shift doctors "
                                    f"and covers {len(t['covers'])} short hospital(s)")
            rec["why_not"].append("Escalation not needed: an eligible doctor exists (escalate only if nobody qualifies)")
        else:
            rec.update(action="escalate", detail="No off-shift doctor passes every rule: clinical lead decides")
            rec["rationale"].append("Nobody qualifies for a split shift; the answer is to escalate, not bend a rule")

    if kind == "consult":
        hid, holder = problem["hospital_id"], problem.get("physician_id")
        stage = problem["stage"]
        if stage == "CT/CTA or Imaging Pending":
            rec.update(action="alert", target=holder,
                       detail=f"Alert {pnames.get(holder, holder)} and coordinator: CT/CTA overdue, chase imaging")
            rec["rationale"].append("Delay is on imaging, not physician capacity: moving the consult would not help")
        else:
            cands = reassign_candidates(state, ref, hid, holder)
            if len(cands):
                c = cands.iloc[0]
                rec.update(action="reassign", target=c["physician_id"],
                           detail=f"Reassign {problem['consult_id']} to {pnames.get(c['physician_id'])}")
                rec["rationale"].append(f"{pnames.get(c['physician_id'])} is credentialed here, "
                                        f"{c['derived_state'].lower()}, {c['open_consults']} open consults")
                rec["why_not"].append("Split shift is slower (offer, approval, time to get online)")
                if holder:
                    rec["rationale"].append(f"{pnames.get(holder, holder)} will be informed after the move")
            else:
                rec.update(action="escalate",
                           detail=f"Escalate {problem['consult_id']}: no credentialed doctor is free now")
                rec["why_not"].append("Reassign impossible: every credentialed doctor is busy or unavailable ("
                                      + "; ".join(_why_not_busy(state, ref, hid, holder)) + ")")
                if len(eligible[eligible["covers"].apply(lambda c: hid in c)]):
                    rec["why_not"].append("A split shift is also proposed for this hospital, "
                                          "but takes ≥15 min to come online, too slow for this patient alone")
    elif kind == "gap":
        split_or_escalate(problem["hospital_id"])
        rec["why_not"].append("Reassigning cannot fix a gap: no on-shift doctor credentialed there stays on")
    elif kind == "note":
        rec.update(action="alert", target=problem["physician_id"],
                   detail=f"Alert {pnames.get(problem['physician_id'])}: note overdue for {problem['consult_id']}")
    elif kind == "shift_end":
        rec.update(action="alert", target=problem["physician_id"],
                   detail=f"Alert coordinator: protect {pnames.get(problem['physician_id'])} from new calls "
                          "so notes are finished before shift end")
    elif kind == "surge":
        rec.update(action="alert", detail="Requirement raised by 1; see coverage gap for the fix")
    elif kind == "signal":
        rec.update(action="alert", target=problem["physician_id"],
                   detail=f"Coordinator verifies {pnames.get(problem['physician_id'])}'s real availability")
    elif kind == "data":
        rec.update(action="alert", detail="Check the event feed")

    rec["label"] = ACTION_LABEL[rec["action"]]
    rec["raci"] = RACI[rec["action"]]
    rec["approver"] = RACI[rec["action"]]["A"]
    return rec
