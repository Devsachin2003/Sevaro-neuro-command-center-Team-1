# Design decisions

Rules the engine and dashboard follow, with their source. HB = Challenge 2 Handbook, Brief = Challenge 2 brief v2, Mentor = Sevaro mentor answers (10 Oct 2026).

## RACI per intervention (Mentor Q1, Q2, Q3, Q6)

| Intervention | Responsible | Accountable (approves) | Consulted | Informed |
|---|---|---|---|---|
| Internal alert | System (automatic) | none | none | Coordinator |
| Reassign consult | Coordinator | Coordinator (no approval) | none | Original physician (after), Quality Leadership |
| Split-shift offer | Coordinator (proposes) | Clinical lead (sole approver) | Ops Leadership | Quality Leadership |
| Escalation | Coordinator | Clinical lead (authorizes) | Ops Leadership | Quality Leadership |
| Approval not answered in time | System | Next tier in the leadership chain | none | Coordinator |

- The original physician is **told after** a reassignment. They are not asked first. (Mentor Q2)
- The clinical lead approves split shifts **without input from anyone else**. (Mentor Q3)
- If the clinical lead doesn't respond in time, the request **moves up the chain of command**. The demo uses a timer that escalates to a "backup approver". (Mentor Q5)

## Hospitals are not in the loop (Mentor Q4)
- Hospitals are not told about reassignments or incoming doctors. Live insight is for Sevaro clinical and ops leadership only.
- Hospitals see who completed a consult only in a monthly report. **No hospital-facing notifications.**

## Offer log = structured event log (Mentor Q7)
- It is used for auditing, for process-improvement analysis, **and** as triggers for messaging and paging systems.
- So it is append-only, machine-readable records (one row per state change: proposed, approved/rejected, sent, accepted/declined/expired, superseded), not free text.
- Each record holds: who was considered, who was excluded and why, the approver, timestamps and the outcome (HB p.15).

## Root cause to frame the pitch around (Mentor Q8)
- The main problem is **staffing during unforeseen surges concentrated in a few hospital systems, where credentialing limits who can help.**
- Clearer ownership helps as the network scales.
- What this means for the dashboard: show **credential depth** per hospital (how many credentialed doctors, and how many will still be on after shift ends), not just raw headcount.

## Engine defaults for open questions (change here if mentors answer differently)

| Question | Default in code | Where |
|---|---|---|
| N3: what is "now" after new data? | `max(18:20, latest event_time)`, plus the +1/+5 min clock buttons | `app.py: base_now()` |
| N4: do scheduled night starts count? | Yes, from their start time (12 h shift) | `coverage.py: shift_roster()` |
| N1: repeat calls | Dedupe only by `event_id`; never merge different `consult_id`s | `replay.py` |
| B5: fresh approval after a decline? | Yes. A decliner is excluded for the day, and the next candidate needs a new approval. Nobody left → escalate | `decide.py`, `act.py` |
| E1: ranking eligible doctors | Most gap hospitals covered → fastest online → fewest hours in 7 days → fewest recent declines | `decide.py: eligibility()` |
| E4: two doctors accept | First acceptance wins; the second is told "already filled"; both are logged | `act.py: respond()` |
| Offer length | 3 h from now (rounded to 5 min); value = 3 h minus time to get online | `decide.py: OFFER_HOURS` |
| Approval timeout | 5 min, then goes to a backup approver (leadership chain) | `act.py: APPROVAL_TIMEOUT_MIN` |
| Surge in projection | A surge seen now is assumed to persist across the 2 h horizon | `coverage.py` |
| Temporarily Unavailable | Not counted toward coverage until their status changes | `coverage.py` |
| Split-shift trap | Joiner is routable only at gap hospitals and seeded at the on-shift median consult count | `routing_sim.py`, `act.py` |
| Documentation stage | Interruptible, so the doctor counts as available for routing | `replay.py: derive_state()` |
