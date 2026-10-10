"""[8] Action store: the only stateful part. Runs the Act loop and keeps an
append-only, machine-readable audit log (mentors: used for audit, process
improvement, and as triggers for messaging/paging systems).

Split-shift offer lifecycle:
  pending_approval --(no answer in APPROVAL_TIMEOUT_MIN)--> pending_backup
  pending_approval|pending_backup --approve--> sent --> accepted | declined | expired
                                   --reject--> rejected
  any open offer --(gap filled by someone else)--> cancelled / no_longer_needed
"""
import itertools
import json
from datetime import datetime

import pandas as pd

APPROVAL_TIMEOUT_MIN = 5
OFFER_EXPIRY_MIN = 10
OPEN_STATUSES = {"pending_approval", "pending_backup", "sent"}
PHI_MARKERS = ("CON-", "stroke", "patient", "NIHSS")


def build_offer_message(hospital_names: list[str], start, end, online_at) -> str:
    """Hospital names, hours and start time only. No patient information."""
    msg = (f"Sevaro split-shift offer: cover {', '.join(hospital_names)} "
           f"from {start:%H:%M} to {end:%H:%M} ET ({(end - start).total_seconds() / 3600:g} h). "
           f"Please be online by {online_at:%H:%M}. You were asked because you are credentialed at "
           f"these hospitals, opted in to split shifts, and are rested. Reply within {OFFER_EXPIRY_MIN} min.")
    assert not any(m.lower() in msg.lower() for m in PHI_MARKERS), "offer message must not carry patient details"
    return msg


class ActionStore:
    def __init__(self):
        self.proposals: dict[str, dict] = {}
        self.log: list[dict] = []
        self.synthetic_events: list[dict] = []
        self.accepted_shifts: list[dict] = []
        self.alerted: set[str] = set()
        self._ids = itertools.count(1)

    # ---------- logging ----------
    def _log(self, now, actor, action, proposal=None, **details):
        self.log.append({
            "sim_time": now.strftime("%H:%M:%S"), "logged_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "actor": actor, "action": action,
            "proposal_id": proposal["id"] if proposal else "",
            "kind": proposal["kind"] if proposal else "",
            "physician_id": (proposal or {}).get("physician_id") or details.pop("physician_id", ""),
            "hospitals": ", ".join((proposal or {}).get("hospitals", [])) or details.pop("hospitals", ""),
            "details": json.dumps(details, default=str),
        })

    def log_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.log, columns=["sim_time", "logged_at", "actor", "action", "proposal_id",
                                               "kind", "physician_id", "hospitals", "details"])

    # ---------- queries ----------
    def open_for(self, problem_id: str) -> dict | None:
        return next((p for p in self.proposals.values()
                     if p["problem_id"] == problem_id and p["status"] in OPEN_STATUSES), None)

    def offer_counts(self) -> dict:
        counts: dict[str, dict] = {}
        for p in self.proposals.values():
            if p["kind"] != "split_shift":
                continue
            c = counts.setdefault(p["physician_id"], {"open": 0, "week": 0, "declined_today": 0})
            c["open"] += p["status"] in OPEN_STATUSES
            c["week"] += p["status"] not in {"pending_approval", "pending_backup", "rejected"}
            c["declined_today"] += p["status"] in {"declined", "expired"}
        return counts

    def pending_approvals(self) -> list[dict]:
        return [p for p in self.proposals.values() if p["status"] in {"pending_approval", "pending_backup"}]

    def sent_offers(self) -> list[dict]:
        return [p for p in self.proposals.values() if p["kind"] == "split_shift" and p["status"] == "sent"]

    # ---------- automatic, low-risk ----------
    def auto_alert(self, problem: dict, now):
        if problem["problem_id"] not in self.alerted:
            self.alerted.add(problem["problem_id"])
            self._log(now, "System", "internal_alert", problem_id=problem["problem_id"],
                      title=problem["title"], detail=problem["rec"]["detail"])

    # ---------- coordinator ----------
    def reassign(self, problem: dict, to_pid: str, now):
        cid = problem["consult_id"]
        self.synthetic_events.append({
            "event_id": f"SIM-{next(self._ids):04d}", "event_time": now, "ingested_at": now,
            "consult_id": cid, "event_type": "physician_assigned", "physician_id": to_pid,
            "hospital_id": problem["hospital_id"], "description": "Consult reassigned by coordinator"})
        self._log(now, "Coordinator", "reassigned", physician_id=to_pid, hospitals=problem["hospital_id"],
                  consult_id=cid, from_physician=problem.get("physician_id") or "Unassigned")
        if problem.get("physician_id"):
            self._log(now, "System", "informed_original_physician", physician_id=problem["physician_id"],
                      consult_id=cid, note="Informed after reassignment (mentor answer Q2)")

    def propose_split_shift(self, problem: dict, candidate: dict, eligibility_df: pd.DataFrame,
                            hospital_names: dict, now) -> dict:
        hosp = list(candidate["covers"])
        p = {"id": f"OFR-{next(self._ids):04d}", "kind": "split_shift", "problem_id": problem["problem_id"],
             "title": f"Split shift for {', '.join(hospital_names[h] for h in hosp)}",
             "physician_id": candidate["physician_id"], "physician_name": candidate["name"],
             "hospitals": hosp, "start": candidate["offer_start"], "end": candidate["offer_end"],
             "online_at": candidate["online_at"], "channel": candidate["channel"],
             "message": build_offer_message([hospital_names[h] for h in hosp], candidate["offer_start"],
                                            candidate["offer_end"], candidate["online_at"]),
             "rationale": problem["rec"]["rationale"] + problem["rec"]["why_not"],
             "considered": [{"physician_id": r.physician_id, "eligible": r.eligible,
                             "failed": [f"{k}: {r.rules[k][1]}" for k in r.failed_rules]}
                            for r in eligibility_df.itertuples()],
             "status": "pending_approval", "created_at": now, "approver": "Clinical lead"}
        self.proposals[p["id"]] = p
        self._log(now, "Coordinator", "proposed_split_shift", p, considered=p["considered"],
                  window=f"{p['start']:%H:%M}-{p['end']:%H:%M}")
        return p

    def propose_escalation(self, problem: dict, now) -> dict:
        p = {"id": f"ESC-{next(self._ids):04d}", "kind": "escalate", "problem_id": problem["problem_id"],
             "title": problem["title"], "physician_id": "", "hospitals": [problem.get("hospital_id") or ""],
             "rationale": problem["why"] + problem["rec"]["why_not"], "status": "pending_approval",
             "created_at": now, "approver": "Clinical lead"}
        self.proposals[p["id"]] = p
        self._log(now, "Coordinator", "escalated", p, problem=problem["title"])
        return p

    # ---------- clinical lead / backup ----------
    def approve(self, pid: str, now, by: str = "Clinical lead"):
        p = self.proposals[pid]
        if p["status"] not in {"pending_approval", "pending_backup"}:
            return
        self._log(now, by, "approved", p)
        if p["kind"] == "split_shift":
            p.update(status="sent", sent_at=now, expires_at=now + pd.Timedelta(minutes=OFFER_EXPIRY_MIN),
                     approved_by=by)
            self._log(now, "System", "offer_sent_simulated", p, channel=p["channel"], message=p["message"])
        else:
            p.update(status="authorized", approved_by=by)

    def reject(self, pid: str, now, by: str = "Clinical lead", reason: str = ""):
        p = self.proposals[pid]
        p["status"] = "rejected"
        self._log(now, by, "rejected", p, reason=reason)

    # ---------- off-shift physician ----------
    def respond(self, pid: str, accept: bool, now) -> str:
        p = self.proposals[pid]
        if p["status"] == "expired" or (p["status"] == "sent" and now >= p["expires_at"]):
            p["status"] = "expired"
            self._log(now, p["physician_name"], "late_reply_ignored", p, reply="accept" if accept else "decline")
            return "This offer has expired."
        if p["status"] in {"cancelled", "no_longer_needed"}:
            # Someone else accepted first (or the gap closed): tell them, log it, change nothing.
            self._log(now, p["physician_name"], "accepted_but_already_filled" if accept else "reply_after_close", p)
            return "Thank you, this gap has already been filled. No action needed."
        if p["status"] != "sent":
            self._log(now, p["physician_name"], "reply_after_close", p, status=p["status"])
            return "This offer is no longer open."
        if not accept:
            p["status"] = "declined"
            self._log(now, p["physician_name"], "declined", p)
            return "Declined. Thank you."
        # First acceptance wins: if the gap is already covered, the second acceptor is told so.
        if any(set(s["hospitals"]) >= set(p["hospitals"]) for s in self.accepted_shifts):
            p["status"] = "no_longer_needed"
            self._log(now, p["physician_name"], "accepted_but_already_filled", p)
            return "Thank you, this gap has already been filled. No action needed."
        p["status"] = "accepted"
        self.accepted_shifts.append({"physician_id": p["physician_id"], "start": p["online_at"],
                                     "end": p["end"], "hospitals": p["hospitals"]})
        self._log(now, p["physician_name"], "accepted", p, online_at=f"{p['online_at']:%H:%M}",
                  routing_limited_to=p["hospitals"])
        for other in self.proposals.values():  # stop asking once the gap is filled
            if other is not p and other["kind"] == "split_shift" and other["status"] in OPEN_STATUSES \
                    and set(other["hospitals"]) <= set(p["hospitals"]):
                other["status"] = "cancelled"
                self._log(now, "System", "cancelled_gap_filled", other)
        return "Accepted. You're confirmed. Coverage has been updated."

    # ---------- timers ----------
    def tick(self, now):
        for p in self.proposals.values():
            if p["status"] == "pending_approval" and now >= p["created_at"] + pd.Timedelta(minutes=APPROVAL_TIMEOUT_MIN):
                p.update(status="pending_backup", approver="Backup approver (leadership chain)")
                self._log(now, "System", "approval_timeout_escalated_to_backup", p)
            if p["status"] == "sent" and now >= p["expires_at"]:
                p["status"] = "expired"
                self._log(now, "System", "offer_expired", p)
