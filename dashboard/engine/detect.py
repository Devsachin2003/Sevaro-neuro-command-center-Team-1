"""[4] Detector: find and rank everything that needs attention, each with a plain reason.

Every problem answers: what is going on, where, why it matters (time against its
limit, and whether it gets worse when shifts end). The decider adds the fix.
"""
import pandas as pd

# (stage, applies_to) -> (warning_min, critical_min, label). Synthetic standards from the brief.
STAGE_LIMITS = {
    ("Triaged", "All"): (5, 10, "Triaged, no doctor assigned"),
    ("Assigned", "Stroke"): (3, 8, "Assigned, physician not yet contacted"),
    ("CT/CTA or Imaging Pending", "Stroke"): (12, 20, "Waiting for CT/CTA"),
    ("Imaging Review", "Stroke"): (6, 12, "Imaging back, video not started"),
}
NOTE_LIMITS = {"Stroke": (30, 45), "General Neuro": (45, 90)}
NOTE_STAGES = ("Note Pending", "Documentation")
SEVERITY_SCORE = {"critical": 1000, "warning": 500, "info": 100}
STALE_DATA_MIN = 10


def _limit_for(consult) -> tuple[float, int, int, str] | None:
    """Returns (minutes_waited, warning, critical, label) for the consult's current clock."""
    stage, ctype = consult.current_stage, consult.consult_type
    if stage in NOTE_STAGES and consult.note_clock_start is not None and not pd.isna(consult.note_clock_start):
        w, c = NOTE_LIMITS[ctype]
        return consult.note_clock_minutes, w, c, "Note not finished after call/video"
    for applies in (ctype, "All"):
        if (stage, applies) in STAGE_LIMITS:
            w, c, label = STAGE_LIMITS[(stage, applies)]
            return consult.stage_minutes_exact, w, c, label
    return None


def _problem(pid, kind, severity, title, where, why, score_extra=0.0, **extra):
    return {"problem_id": pid, "kind": kind, "severity": severity, "title": title,
            "where": where, "why": why, "score": SEVERITY_SCORE[severity] + score_extra, **extra}


def detect(state: dict, ref: dict, coverage: dict) -> list[dict]:
    now = state["now"]
    consults = state["consults"].copy()
    phys = state["physicians"].set_index("physician_id")
    names = ref["hospitals"].set_index("hospital_id")["hospital_name"].to_dict()
    pnames = pd.concat([ref["on_shift"], ref["off_shift"]]).set_index("physician_id")["name"].to_dict()
    summary = coverage["summary"].set_index("hospital_id")
    ending_soon = set(phys.index[phys["minutes_to_shift_end"] <= 60])
    problems = []

    consults["stage_minutes_exact"] = (now - consults["stage_entered_at"]).dt.total_seconds() / 60
    consults["note_clock_minutes"] = (now - pd.to_datetime(consults["note_clock_start"])).dt.total_seconds() / 60

    # ---- consult-level problems ----
    for c in consults.itertuples(index=False):
        reasons, severity, over = [], None, 0.0
        lim = _limit_for(c)
        if lim:
            waited, w, crit, label = lim
            if waited >= crit:
                severity, over = "critical", waited - crit
                reasons.append(f"{label}: {waited:.0f} min, critical limit is {crit} min")
            elif waited >= w:
                severity, over = "warning", waited - w
                reasons.append(f"{label}: {waited:.0f} min, warning at {w} min (critical {crit})")
        unassigned = c.physician_id == "Unassigned"
        if unassigned and c.routing_failures:
            reasons.append(f"Routing failed {c.routing_failures}x: no free credentialed doctor "
                           f"at {names.get(c.hospital_id, c.hospital_id)}")
            severity = severity or "warning"  # escalates to critical via the stage clock
        holder = c.physician_id
        if not unassigned and holder in phys.index and phys.loc[holder, "derived_state"] == "Temporarily Unavailable":
            reasons.append(f"Assigned doctor {pnames.get(holder, holder)} is Temporarily Unavailable")
            severity = severity or "warning"
        if not unassigned and holder in ending_soon and c.current_stage not in NOTE_STAGES:
            reasons.append(f"{pnames.get(holder, holder)}'s shift ends in "
                           f"{phys.loc[holder, 'minutes_to_shift_end']} min")
        if not severity:
            continue
        hosp = summary.loc[c.hospital_id] if c.hospital_id in summary.index else None
        worse = hosp is not None and hosp["first_gap"] is not None and not pd.isna(hosp["first_gap"])
        if worse:
            reasons.append(f"Gets worse: {names[c.hospital_id]} is projected short from "
                           f"{hosp['first_gap']:%H:%M}")
        score = (over * 10 + (200 if c.consult_type == "Stroke" else 0)
                 + (150 if unassigned else 0) + 25 * c.routing_failures + (100 if worse else 0))
        stage_kind = "note" if c.current_stage in NOTE_STAGES else "consult"
        problems.append(_problem(
            f"consult:{c.consult_id}", stage_kind, severity,
            title=(f"{c.consult_type} consult {c.consult_id} "
                   + ("is waiting with no doctor" if unassigned else f"stuck at '{c.current_stage}'")),
            where=f"{names.get(c.hospital_id, c.hospital_id)} · {c.consult_id} · "
                  f"{'Unassigned' if unassigned else pnames.get(holder, holder)}",
            why=reasons, score_extra=score, consult_id=c.consult_id, hospital_id=c.hospital_id,
            physician_id=None if unassigned else holder, consult_type=c.consult_type,
            stage=c.current_stage, minutes_since_received=c.minutes_since_received))

    # ---- physician-level problems ----
    for pid, p in phys.iterrows():
        mine = consults[consults["physician_id"] == pid]
        open_notes = mine[mine["current_stage"].isin(NOTE_STAGES)]
        if p["minutes_to_shift_end"] <= 60 and len(open_notes) >= 1:
            sev = "critical" if len(open_notes) >= 2 else "warning"
            problems.append(_problem(
                f"shiftend:{pid}", "shift_end", sev,
                title=f"{pnames.get(pid, pid)} leaves in {p['minutes_to_shift_end']} min with "
                      f"{len(mine)} open consults ({len(open_notes)} unfinished notes)",
                where=f"{pnames.get(pid, pid)} · {', '.join(mine['consult_id'])}",
                why=[f"Standard: 2+ open notes within 60 min of shift end is critical",
                     f"Open notes: {', '.join(open_notes['consult_id'])}"],
                score_extra=len(mine) * 20, physician_id=pid))
        for flag in p["risk_flags"]:
            problems.append(_problem(
                f"signal:{pid}:{flag[:12]}", "signal", "warning" if "Camera" in flag else "info",
                title=f"{pnames.get(pid, pid)}: signals disagree",
                where=pnames.get(pid, pid),
                why=[flag, f"Self-reported status says '{p['self_reported_status']}'"]
                    + ([f"Routing is ringing them for {p['ringing_consult']}"] if p["ringing_consult"] else []),
                score_extra=50 if p["ringing_consult"] else 0, physician_id=pid))

    # ---- hospital-level problems ----
    for hid, h in summary.iterrows():
        if h["surge"]:
            problems.append(_problem(
                f"surge:{hid}", "surge", "warning",
                title=f"Demand surge at {h['hospital_name']}",
                where=h["hospital_name"],
                why=[f"{h['last_60_min']} consults in the last 60 min vs normal-busy (p90) of {h['p90']:.0f}",
                     "Coverage requirement raised by 1"],
                score_extra=h["last_60_min"] * 10, hospital_id=hid))
        if h["first_gap"] is not None and not pd.isna(h["first_gap"]):
            g = coverage["grid"]
            row = g[(g["hospital_id"] == hid) & (g["time"] == h["first_gap"])].iloc[0]
            minutes_ahead = int((h["first_gap"] - now).total_seconds() // 60)
            problems.append(_problem(
                f"gap:{hid}", "gap", "critical" if minutes_ahead <= 60 else "warning",
                title=f"{h['hospital_name']} will be short of credentialed doctors at {h['first_gap']:%H:%M}",
                where=h["hospital_name"],
                why=[f"At {h['first_gap']:%H:%M}: {row['staffed']} credentialed on shift vs {row['required']} required"
                     + (" (raised by surge)" if row["surge"] else ""),
                     f"In {minutes_ahead} min, after scheduled shift ends"],
                score_extra=300 - minutes_ahead + 100 * h["gap_shortfall"], hospital_id=hid,
                gap_time=h["first_gap"]))

    # ---- data freshness ----
    lag = (now - state["quality"]["last_ingested"]).total_seconds() / 60
    if lag > STALE_DATA_MIN:
        problems.append(_problem("data:stale", "data", "warning",
                                 title=f"No new data for {lag:.0f} min",
                                 where="Event feed", why=["The picture may be out of date"], score_extra=0))

    problems.sort(key=lambda p: -p["score"])
    for rank, p in enumerate(problems, 1):
        p["rank"] = rank
    return problems
