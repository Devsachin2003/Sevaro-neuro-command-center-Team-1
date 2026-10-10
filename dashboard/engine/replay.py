"""Rebuild physician state and consult state from the event stream.

Rules from the brief:
  * dedupe by event_id (the same record can be ingested twice)
  * order by event_time, never by ingested_at (records can arrive late)
  * physician activity and consult lifecycle are tracked independently
  * availability is derived from Synapse call/video, camera and agent signals,
    the self-reported status is only one clue
"""
from dataclasses import dataclass, field

import pandas as pd

STAGES = ["Received", "Triaged", "Assigned", "Provider Contacted",
          "CT/CTA or Imaging Pending", "Imaging Review", "Video Consult In Progress",
          "Note Pending", "Documentation", "Signed Off"]
STAGE_RANK = {s: i for i, s in enumerate(STAGES)}

EVENT_TO_STAGE = {
    "consult_received": "Received",
    "consult_triaged": "Triaged",
    "physician_assigned": "Assigned",
    "incoming_call_started": "Provider Contacted",
    "imaging_requested": "CT/CTA or Imaging Pending",
    "imaging_returned": "Imaging Review",
    "video_started": "Video Consult In Progress",
    "video_ended": "Note Pending",
    "note_started": "Documentation",
    "note_signed": "Signed Off",
}

LATE_THRESHOLD_SEC = 60


@dataclass
class Consult:
    consult_id: str
    hospital_id: str = ""
    consult_type: str = ""
    physician_id: str = ""
    stage: str = ""
    stage_entered_at: pd.Timestamp | None = None
    received_at: pd.Timestamp | None = None
    note_clock_start: pd.Timestamp | None = None  # end of last call/video
    routing_failures: int = 0
    last_routing_failure: pd.Timestamp | None = None
    ringing_physician: str = ""

    def advance(self, stage: str, at: pd.Timestamp) -> None:
        # Forward-only: a stray earlier-stage event never moves a consult backwards.
        if not self.stage or STAGE_RANK[stage] > STAGE_RANK[self.stage]:
            self.stage, self.stage_entered_at = stage, at


@dataclass
class Physician:
    physician_id: str
    status: str = "Available"
    agent_online: bool = True
    agent_lost_at: pd.Timestamp | None = None
    camera_active: bool = False
    call: str = ""          # "incoming" | "outgoing" | ""
    call_consult: str = ""
    video_consult: str = ""
    ringing_consult: str = ""
    last_call_end: pd.Timestamp | None = None
    last_answered: pd.Timestamp | None = None
    shift_started_at: pd.Timestamp | None = None
    consults_this_shift: int = 0
    calls_this_shift: int = 0
    flags: list[str] = field(default_factory=list)


def clean_events(events: pd.DataFrame, now: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Dedupe, drop future events, sort by event_time. Returns the clean frame and data-quality notes."""
    dupes = events[events.duplicated("event_id", keep="first")]
    ev = (events.sort_values("ingested_at")
                .drop_duplicates("event_id", keep="first"))
    ev = ev[ev["event_time"] <= now]
    lag = (ev["ingested_at"] - ev["event_time"]).dt.total_seconds()
    late = ev[lag > LATE_THRESHOLD_SEC].assign(lag_sec=lag[lag > LATE_THRESHOLD_SEC])
    ev = ev.sort_values(["event_time", "ingested_at", "event_id"], kind="stable")
    quality = {
        "duplicates": dupes[["event_id", "event_type", "consult_id", "physician_id"]],
        "late": late[["event_id", "event_type", "consult_id", "physician_id", "event_time", "ingested_at", "lag_sec"]],
        "last_ingested": events["ingested_at"].max(),
    }
    return ev, quality


def replay(events: pd.DataFrame, ref: dict, now: pd.Timestamp,
           extra_shifts: list[dict] | None = None) -> dict:
    """extra_shifts: accepted split-shift offers, each {physician_id, start, end, hospitals}."""
    ev, quality = clean_events(events, now)
    consults: dict[str, Consult] = {}
    phys: dict[str, Physician] = {p: Physician(p) for p in ref["on_shift"]["physician_id"]}
    extra = {s["physician_id"]: s for s in (extra_shifts or [])}
    for pid in extra:
        phys.setdefault(pid, Physician(pid))

    for e in ev.itertuples(index=False):
        t, et, pid, cid = e.event_time, e.event_type, e.physician_id, e.consult_id
        p = phys.setdefault(pid, Physician(pid)) if pid else None
        c = consults.setdefault(cid, Consult(cid)) if cid else None
        if c is not None and e.hospital_id:
            c.hospital_id = e.hospital_id

        # ---- consult lifecycle ----
        if c is not None:
            if et == "consult_received":
                c.received_at = c.received_at or t
                c.consult_type = "Stroke" if "stroke" in e.description.lower() else "General Neuro"
            if et == "physician_assigned":
                c.physician_id = pid
            if et == "routing_attempt_failed":
                c.routing_failures += 1
                c.last_routing_failure = t
            if et == "incoming_call_ringing":
                c.ringing_physician = pid
            if et in ("incoming_call_ended", "outgoing_call_ended", "video_ended"):
                c.note_clock_start = t
            if et == "incoming_call_ended" and "phone-only" in e.description.lower():
                c.advance("Note Pending", t)
            if et in EVENT_TO_STAGE:
                c.advance(EVENT_TO_STAGE[et], t)

        # ---- physician activity ----
        if p is not None:
            if et == "shift_started":
                p.shift_started_at = t
            elif et == "physician_status_changed":
                p.status = e.description.split(":", 1)[-1].strip()
            elif et == "camera_active":
                p.camera_active = True
            elif et == "camera_inactive":
                p.camera_active = False
            elif et == "agent_heartbeat_lost":
                p.agent_online, p.agent_lost_at = False, t
            elif et == "physician_assigned":
                p.consults_this_shift += 1
            elif et == "incoming_call_ringing":
                p.ringing_consult = cid
            elif et in ("incoming_call_started", "outgoing_call_started"):
                p.call, p.call_consult = et.split("_")[0], cid
                p.calls_this_shift += 1
                if et == "incoming_call_started":
                    p.last_answered = t
                if p.ringing_consult == cid:
                    p.ringing_consult = ""
            elif et in ("incoming_call_ended", "outgoing_call_ended"):
                p.call, p.call_consult, p.last_call_end = "", "", t
            elif et == "video_started":
                p.video_consult = cid
            elif et == "video_ended":
                p.video_consult = ""

    consults_df = _consults_frame(consults, now)
    physicians_df = _physicians_frame(phys, consults_df, ref, now, extra)
    return {"consults": consults_df, "physicians": physicians_df,
            "quality": quality, "events": ev, "now": now}


def _minutes(now, t):
    return None if t is None or pd.isna(t) else int((now - t).total_seconds() // 60)


def _consults_frame(consults: dict[str, Consult], now) -> pd.DataFrame:
    rows = []
    for c in consults.values():
        if not c.stage or c.stage == "Signed Off":
            continue
        rows.append({
            "consult_id": c.consult_id, "hospital_id": c.hospital_id,
            "physician_id": c.physician_id or "Unassigned", "consult_type": c.consult_type,
            "current_stage": c.stage, "stage_entered_at": c.stage_entered_at,
            "minutes_in_stage": _minutes(now, c.stage_entered_at),
            "minutes_since_received": _minutes(now, c.received_at),
            "received_at": c.received_at, "note_clock_start": c.note_clock_start,
            "routing_failures": c.routing_failures, "ringing_physician": c.ringing_physician,
        })
    return pd.DataFrame(rows).sort_values("consult_id").reset_index(drop=True)


def _shift_window(pid, p: Physician, ref, now, extra=None):
    if extra and pid in extra:
        return extra[pid]["start"], extra[pid]["end"]
    on = ref["on_shift"].set_index("physician_id")
    day = now.normalize()
    if pid in on.index:
        s, e = on.loc[pid, "shift_start_et"], on.loc[pid, "shift_end_et"]
        start = day + pd.Timedelta(s + ":00")
        end = day + pd.Timedelta(e + ":00")
        return start, end if end > start else end + pd.Timedelta(days=1)
    if p.shift_started_at is not None:  # e.g. a night or split-shift doctor who came online
        return p.shift_started_at, p.shift_started_at + pd.Timedelta(hours=12)
    return None, None


def derive_state(p: Physician, has_documentation: bool) -> tuple[str, bool, list[str]]:
    """Returns (derived_state, available_for_routing, risk_flags)."""
    flags = []
    if not p.agent_online:
        flags.append("Desktop agent offline: availability unverified")
    if p.video_consult:
        return "Video Consult In Progress", False, flags
    if p.call == "incoming":
        return "Incoming Hospital Call", False, flags
    if p.call == "outgoing":
        return "Outgoing Hospital Call", False, flags
    if p.status == "Temporarily Unavailable":
        return "Temporarily Unavailable", False, flags
    if p.camera_active and p.status != "In Meeting":
        flags.append("Camera on with no Synapse session: likely on an external video call")
        return "Camera Active - No Synapse Session", False, flags
    if has_documentation:
        return "Documentation / Note", True, flags
    if p.status == "In Meeting":
        return "In Meeting (interruptible)", True, flags
    return "Available", True, flags


def _physicians_frame(phys, consults_df, ref, now, extra) -> pd.DataFrame:
    rows = []
    for pid, p in sorted(phys.items()):
        start, end = _shift_window(pid, p, ref, now, extra)
        if start is None or not (start <= now < end):
            continue
        mine = consults_df[consults_df["physician_id"] == pid]
        state, available, flags = derive_state(p, (mine["current_stage"] == "Documentation").any())
        if p.ringing_consult:
            available = False
        rows.append({
            "physician_id": pid,
            "shift_et": f"{start:%H:%M}-{end:%H:%M}",
            "self_reported_status": p.status,
            "desktop_agent": "Online" if p.agent_online else "Offline",
            "derived_state": state,
            "available_for_routing": available,
            "consults_this_shift": p.consults_this_shift,
            "calls_this_shift": p.calls_this_shift,
            "open_consults": len(mine),
            "minutes_to_shift_end": _minutes(end, now),
            "shift_end": end,
            "ringing_consult": p.ringing_consult,
            "last_call_end": p.last_call_end,
            "last_answered": p.last_answered,
            "risk_flags": flags,
            "split_shift_hospitals": extra[pid]["hospitals"] if pid in extra else None,
        })
    return pd.DataFrame(rows)


def shift_window(pid: str, ref: dict, now: pd.Timestamp, extra_shifts=None):
    """Public helper for projection: (start, end) of a physician's current/today shift."""
    extra = {s["physician_id"]: s for s in (extra_shifts or [])}
    return _shift_window(pid, Physician(pid), ref, now, extra)
