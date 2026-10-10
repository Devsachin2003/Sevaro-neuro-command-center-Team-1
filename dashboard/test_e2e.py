"""End-to-end checks for the whole Detect -> Decide -> Act loop.

Run:  python test_e2e.py
"""
import pandas as pd

from engine.act import ActionStore
from engine.loader import ET, load_events, load_reference, parse_events
from engine.pipeline import analyze

REF = load_reference()
NOW = pd.Timestamp("2026-03-17 18:20:00", tz=ET)
HOSP = REF["hospitals"].set_index("hospital_id")["hospital_name"].to_dict()
EVENTS = load_events()


def mins(n):
    return NOW + pd.Timedelta(minutes=n)


def propose_top_split(store, now, events=EVENTS):
    a = analyze(events, REF, now, store.accepted_shifts, store.offer_counts())
    gap = next(p for p in a["problems"] if p["rec"]["action"] == "split_shift")
    elig = a["eligibility"]
    cand = elig[elig["physician_id"] == gap["rec"]["target"]].iloc[0]
    return store.propose_split_shift(gap, cand, a["eligibility"], HOSP, now)


def test_detect_and_decide():
    a = analyze(EVENTS, REF, NOW)
    top = a["problems"][0]
    assert top["problem_id"] == "consult:CON-2105", top["title"]
    assert top["rec"]["action"] == "escalate"
    assert {p["hospital_id"] for p in a["problems"] if p["kind"] == "gap"} == {"SYN-005", "SYN-008"}
    elig = a["eligibility"]
    assert list(elig.loc[elig["eligible"], "physician_id"]) == ["PHY-009"]
    fails = dict(zip(elig["physician_id"], elig["failed_rules"]))
    assert fails["PHY-010"] == ["Credentialed at gap"]
    assert fails["PHY-011"] == ["No shift clash"]
    assert "Opted in" in fails["PHY-012"] and "Opted in" in fails["PHY-013"]
    assert fails["PHY-014"] == ["≤60h in 7 days"]
    t = a["trap"]
    assert t["joiner_unrestricted"] > t["joiner_restricted"]
    assert set(t["joiner_restricted_hospitals"]) <= set(a["joiner"]["hospitals"])


def test_full_loop_closes_gap():
    s = ActionStore()
    o = propose_top_split(s, NOW)
    assert "CON-" not in o["message"] and "stroke" not in o["message"].lower()
    s.approve(o["id"], mins(1))
    assert o["status"] == "sent"
    assert s.respond(o["id"], True, mins(3)).startswith("Accepted")
    a = analyze(EVENTS, REF, mins(3), s.accepted_shifts, s.offer_counts())
    assert not [p for p in a["problems"] if p["kind"] == "gap"], "gaps should be filled"
    actions = list(s.log_frame()["action"])
    assert actions == ["proposed_split_shift", "approved", "offer_sent_simulated", "accepted"]


def test_decline_leads_to_escalation():
    s = ActionStore()
    o = propose_top_split(s, NOW)
    s.approve(o["id"], mins(1))
    s.respond(o["id"], False, mins(2))
    a = analyze(EVENTS, REF, mins(2), s.accepted_shifts, s.offer_counts())
    gap = next(p for p in a["problems"] if p["kind"] == "gap")
    assert gap["rec"]["action"] == "escalate", "nobody else qualifies, so escalate instead of bending a rule"


def test_timers_and_double_accept():
    s = ActionStore()
    o = propose_top_split(s, NOW)
    s.tick(mins(6))
    assert o["status"] == "pending_backup", "approval timeout escalates to backup"
    s.approve(o["id"], mins(6), "Backup approver")
    s.tick(mins(17))
    assert o["status"] == "expired"
    assert s.respond(o["id"], True, mins(18)) == "This offer has expired."
    # Two acceptances for the same gap: the first wins, the second is told it is no longer needed.
    s2 = ActionStore()
    o1 = propose_top_split(s2, NOW)
    o2 = dict(o1, id="OFR-9999", physician_id="PHY-X", physician_name="Second doctor", status="sent",
              expires_at=mins(30))
    s2.proposals[o2["id"]] = o2
    s2.approve(o1["id"], mins(1))
    s2.respond(o1["id"], True, mins(2))
    assert "already been filled" in s2.respond(o2["id"], True, mins(2))


def test_judge_file_injection():
    new = parse_events(pd.read_csv("demo/judge_test_events.csv"))
    combined = pd.concat([EVENTS, new], ignore_index=True)
    now = max(NOW, combined["event_time"].max().ceil("min"))
    a = analyze(combined, REF, now)
    q = a["state"]["quality"]
    assert "EVT-01163" in set(q["duplicates"]["event_id"])
    assert "EVT-01168" in set(q["late"]["event_id"])
    c = a["state"]["consults"].set_index("consult_id")
    assert c.loc["CON-2119", "physician_id"] == "Unassigned"
    assert c.loc["CON-2101", "current_stage"] == "Note Pending"
    assert c.loc["CON-2109", "physician_id"] == "PHY-007"
    assert any(p["problem_id"] == "consult:CON-2119" for p in a["problems"])
    phys = a["state"]["physicians"].set_index("physician_id")
    assert phys.loc["PHY-008", "derived_state"] != "Temporarily Unavailable"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"PASS {name}")
