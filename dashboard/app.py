"""Sevaro Operations Command Center: Detect -> Decide -> Act.

Run:  streamlit run app.py
"""
import html
import io

import pandas as pd
import streamlit as st

from engine.act import ActionStore
from engine.decide import RACI
from engine.loader import ET, EVENT_COLUMNS, load_events, load_reference, parse_events
from engine.pipeline import analyze
from ui_theme import apply_theme, eyebrow, header

DEFAULT_NOW = pd.Timestamp("2026-03-17 18:20:00", tz=ET)
SEV = {"critical": "🔴 CRITICAL", "warning": "🟠 WARNING", "info": "🔵 INFO"}
GAP_BG, GAP_FG = "#d03b3b", "#ffffff"   # status palette: critical
FREE_LOW_BG = "#fab219"                  # status palette: warning

st.set_page_config(page_title="Sevaro Command Center", page_icon="🧠", layout="wide")
apply_theme()


# ---------------------------------------------------------------- state
@st.cache_data
def reference():
    return load_reference()


def init_state(force=False):
    if force or "events" not in st.session_state:
        st.session_state.events = load_events()
        st.session_state.store = ActionStore()
        st.session_state.offset_min = 0
        st.session_state.loaded_files = set()
        st.session_state.phone_reply = {}
        st.session_state.uploader_key = st.session_state.get("uploader_key", 0) + 1


init_state()
ref = reference()
store: ActionStore = st.session_state.store
HOSP = ref["hospitals"].set_index("hospital_id")["hospital_name"].to_dict()
PNAME = pd.concat([ref["on_shift"], ref["off_shift"]]).set_index("physician_id")["name"].to_dict()


def all_events() -> pd.DataFrame:
    ev = st.session_state.events
    if store.synthetic_events:
        ev = pd.concat([ev, pd.DataFrame(store.synthetic_events)[EVENT_COLUMNS]], ignore_index=True)
    return ev


def base_now() -> pd.Timestamp:
    """'Now' is 18:20 for the sample; if new data moves the day forward, now follows the
    latest event_time (default for open question N3)."""
    latest = st.session_state.events["event_time"].max().ceil("min")
    return max(DEFAULT_NOW, latest)


# ---------------------------------------------------------------- help
@st.dialog("Tutorial & Help", width="large")
def show_help():
    st.markdown("""
**What this tool does.** It reads Synapse events, works out what needs attention now and in the
next 2 hours (**Detect**), recommends the fix and who approves it (**Decide**), and runs the fix
through approval to the doctor's reply (**Act**).

#### Workflow in 6 steps
1. **🧭 Operations coordinator tab: read the top card.** *Act on this first* is the #1 problem: what, where,
   why it matters, the recommended fix, who approves it, and why that fix beats the others.
2. **Scan the ranked list** below it. 🔴 Critical → 🟠 Warning → 🔵 Info. Expand any item for details
   and its action button.
3. **Check the coverage grid.** Each cell is *credentialed doctors on shift / required*.
   **Red ▲ short** = a projected gap. **Amber** in the *Now* column = nobody credentialed is free right now.
   ⚡ = demand surge.
4. **Act.** *Reassign* runs immediately (the coordinator owns it). *Propose offer* and *Escalate* go to
   the **🩺 Clinical lead** tab for approval. Low-risk alerts are sent automatically.
5. **🩺 Clinical lead tab: approve or reject.** It shows the exact message the doctor will get (no patient
   details) and every off-shift doctor considered. If nobody answers within 5 min, it goes to a backup approver.
6. **📱 Off-shift neurologist tab: accept or decline.** Accept → the doctor is added to coverage (only at the
   short hospitals), the red cells clear, and other offers for that gap stop. Decline → next eligible
   doctor or escalate. Everything is recorded in the **📜 Audit log** (downloadable CSV).

#### Sidebar controls
- **Now (ET):** the scenario clock (18:20 in the sample), not your computer's clock. **+1 / +5 min**
  moves time forward to show timers, offer expiry and approval timeouts.
- **Load new events:** drop a CSV in the `event_stream` format. It is added to the day and everything
  is recalculated, with no restart. Duplicate and late records are handled automatically.
- **Reset demo:** reloads the sample data and clears all offers and logs.

#### Terms
| Term | Meaning |
|---|---|
| Consult | One hospital's request for a neurologist for one patient |
| Stroke / General Neuro | Consult types; stroke has the tightest time limits |
| Credentialed | Approved to take consults from that hospital |
| Derived state | What a doctor is actually doing, inferred from call, video, camera and agent signals (not their self-set status) |
| Agent offline | Desktop helper stopped reporting, so availability is unverified |
| Coverage gap | Fewer credentialed doctors than the hospital's minimum, now or soon |
| Surge | Last-60-min consults above the hospital's normal-busy level (p90) for that hour; raises the minimum by 1 |
| p90 | Normal-busy level: 9 in 10 past weeks were at or below it |
| Split shift | An off-duty doctor works a few extra hours to cover a gap |
| Split-shift trap | Routing would send a newly joined doctor almost every call; we limit them to the short hospitals |
| Escalate | Hand the problem to the clinical lead when the normal fix isn't available |
| RACI | Responsible · Accountable (approves) · Consulted · Informed |

#### Time limits (warning / critical)
Triaged, no doctor: 5 / 10 min · Assigned, not contacted (stroke): 3 / 8 · Waiting for CT/CTA (stroke): 12 / 20 ·
Scans back, no video (stroke): 6 / 12 · Note after call/video: stroke 30 / 45, general 45 / 90 ·
2+ open notes within 60 min of shift end: critical.

#### Split-shift rules (all must pass)
Opted in · credentialed at a short hospital · 10 h rest since last shift · 7-day hours + offer ≤ 60 ·
no clash with a scheduled shift · worth ≥ 2 h after time to get online · one open offer and ≤ 3 offers a week.
Offers expire after 10 min; the first acceptance wins.
""")
    st.caption("All data is synthetic. Thresholds are illustrative, not Sevaro's production settings.")


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    eyebrow("Scenario controls")
    now = base_now() + pd.Timedelta(minutes=st.session_state.offset_min)
    st.metric("Now (ET)", now.strftime("%H:%M"), help="Scenario clock, not the computer clock")
    c1, c2, c3 = st.columns(3)
    if c1.button("+1 min"):
        st.session_state.offset_min += 1; st.rerun()
    if c2.button("+5 min"):
        st.session_state.offset_min += 5; st.rerun()
    if c3.button("Reset"):
        st.session_state.offset_min = 0; st.rerun()

    st.subheader("Load new events")
    files = st.file_uploader("Drop an event_stream-format CSV", type="csv", accept_multiple_files=True,
                             key=f"uploader_{st.session_state.uploader_key}")
    for f in files or []:
        sig = (f.name, f.size)
        if sig in st.session_state.loaded_files:
            continue
        try:
            new = parse_events(pd.read_csv(io.BytesIO(f.getvalue())))
        except Exception as exc:  # bad file: tell the user, keep running
            st.error(f"{f.name}: {exc}")
            continue
        known = set(st.session_state.events["event_id"])
        fresh = (~new["event_id"].isin(known)).sum()
        st.session_state.events = pd.concat([st.session_state.events, new], ignore_index=True)
        st.session_state.loaded_files.add(sig)
        store._log(now, "System", "data_loaded", file=f.name, rows=len(new), new_events=int(fresh),
                   duplicates=int(len(new) - fresh))
        st.toast(f"Loaded {f.name}: {fresh} new events, {len(new) - fresh} duplicates ignored")
        st.rerun()

    ev = st.session_state.events
    st.caption(f"{ev['event_id'].nunique():,} unique events · last ingested "
               f"{ev['ingested_at'].max():%H:%M:%S}")
    if st.button("Reset demo", type="secondary"):
        init_state(force=True); st.rerun()
    if st.button("❓ Tutorial / Help", type="tertiary", help="Opens a guide over this page"):
        show_help()

# ---------------------------------------------------------------- engine
now = base_now() + pd.Timedelta(minutes=st.session_state.offset_min)
store.tick(now)
A = analyze(all_events(), ref, now, store.accepted_shifts, store.offer_counts())
problems, cov, state, elig = A["problems"], A["coverage"], A["state"], A["eligibility"]
for p in problems:
    if p["rec"]["action"] == "alert" and p["severity"] == "critical":
        store.auto_alert(p, now)


# ---------------------------------------------------------------- shared renderers
def open_split_for(hospital_id):
    return next((o for o in store.proposals.values() if o["kind"] == "split_shift"
                 and o["status"] in {"pending_approval", "pending_backup", "sent", "accepted"}
                 and hospital_id in o["hospitals"]), None)


def action_area(p, key):
    rec = p["rec"]
    act = rec["action"]
    if act == "reassign":
        if st.button(f"Reassign to {PNAME.get(rec['target'])}", key=f"ra_{key}", type="primary"):
            store.reassign(p, rec["target"], now); st.rerun()
    elif act == "split_shift":
        existing = open_split_for(p["hospital_id"])
        if existing:
            st.info(f"Offer {existing['id']} to {existing['physician_name']}: **{existing['status'].replace('_', ' ')}**")
        elif st.button(f"Propose offer to {PNAME.get(rec['target'])} → clinical lead", key=f"ss_{key}", type="primary"):
            cand = elig[elig["physician_id"] == rec["target"]].iloc[0]
            store.propose_split_shift(p, cand, elig, HOSP, now); st.rerun()
    elif act == "escalate":
        existing = store.open_for(p["problem_id"]) or next(
            (o for o in store.proposals.values() if o["problem_id"] == p["problem_id"] and o["status"] == "authorized"), None)
        if existing:
            st.info(f"Escalation {existing['id']}: **{existing['status'].replace('_', ' ')}**")
        elif st.button("Escalate to clinical lead", key=f"es_{key}", type="primary"):
            store.propose_escalation(p, now); st.rerun()
    else:
        st.caption("🔔 Internal alert: sent automatically (low-risk, no approval needed)")


def problem_body(p, key):
    rec = p["rec"]
    st.markdown(f"**Where:** {p['where']}")
    st.markdown("**Why it matters:**\n" + "\n".join(f"- {w}" for w in p["why"]))
    st.markdown(f"**Do this:** {rec['label']}: {rec['detail']}  \n**Who approves:** {rec['approver']}")
    if rec["rationale"] or rec["why_not"]:
        st.markdown("**Why this fix:**\n" + "\n".join(f"- {r}" for r in rec["rationale"] + rec["why_not"]))
    r = rec["raci"]
    st.caption(f"RACI · Responsible: {r['R']} · Accountable: {r['A']} · Consulted: {r['C']} · Informed: {r['I']}")
    action_area(p, key)


def coverage_table():
    g = cov["grid"].copy()
    times = sorted(g["time"].unique())
    surge = cov["summary"].set_index("hospital_id")["surge"]
    labels = {t: (f"Now {t:%H:%M}" if t == now else f"{t:%H:%M}") for t in times}

    def cell(r):
        if r["time"] == now:
            return f"{int(r['free_now'])} free · {r['staffed']}/{r['required']}"
        return f"{r['staffed']}/{r['required']}" + ("  ▲ short" if r["gap"] else "")

    g["cell"] = g.apply(cell, axis=1)
    g["row"] = g["hospital_id"].map(lambda h: HOSP[h] + ("  ⚡surge" if surge[h] else ""))
    text = g.pivot(index="row", columns="time", values="cell")
    gap = g.pivot(index="row", columns="time", values="gap")
    free_short = g[g["time"] == now].set_index("row")["short_now"]
    order = g.groupby("row")["gap"].any().sort_values(ascending=False).index
    text, gap = text.loc[order], gap.loc[order]
    text.index.name = "Hospital"

    def colour(_):
        out = pd.DataFrame("", index=text.index, columns=text.columns)
        for col in text.columns:
            for row in text.index:
                if gap.loc[row, col]:
                    out.loc[row, col] = f"background-color:{GAP_BG};color:{GAP_FG};font-weight:600"
                elif col == now and bool(free_short.get(row, False)):
                    out.loc[row, col] = f"background-color:{FREE_LOW_BG};color:#1a1a19"
        return out

    styled = text.rename(columns=labels).style.apply(
        lambda d: colour(d).rename(columns=labels), axis=None)
    st.dataframe(styled, width="stretch")
    st.caption("Cells: credentialed doctors on shift / required (surge adds 1). "
               "Red ▲ = projected short. Amber in the Now column = nobody credentialed is free right now. "
               "Scheduled night starts count from their start time; accepted split shifts count only at their gap hospitals.")


# ---------------------------------------------------------------- header + tabs
header(now.strftime("%H:%M"), sum(p["severity"] == "critical" for p in problems),
       sum(p["kind"] == "gap" for p in problems))
tab_coord, tab_lead, tab_phone, tab_log = st.tabs(
    ["🧭 Operations coordinator", f"🩺 Clinical lead ({len(store.pending_approvals())})",
     f"📱 Off-shift neurologist ({len(store.sent_offers())})", "📜 Audit log"])

with tab_coord:
    crit = [p for p in problems if p["severity"] == "critical"]
    waiting = [p for p in problems if p["kind"] == "consult" and p.get("physician_id") is None
               and p.get("consult_type") == "Stroke"]
    gaps = [p for p in problems if p["kind"] == "gap"]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Critical", len(crit))
    k2.metric("Stroke · no doctor", len(waiting))
    k3.metric("Short in 2 h", len(gaps))
    k4.metric("Open consults", len(state["consults"]))

    if problems:
        top = problems[0]
        with st.container(border=True, key="topcard"):
            eyebrow(f"Act on this first · {SEV[top['severity']]}")
            st.markdown(f"### {top['title']}")
            problem_body(top, "top")

    left, right = st.columns([1.05, 1])
    with left:
        eyebrow("Detect")
        st.subheader("Everything that needs attention, ranked")
        for p in problems[1:]:
            with st.expander(f"#{p['rank']} · {SEV[p['severity']]} · {p['title']}",
                             expanded=p["rec"]["action"] in {"split_shift"} and not open_split_for(p.get("hospital_id"))):
                problem_body(p, p["problem_id"])
    with right:
        eyebrow("Coverage projection")
        st.subheader("Credentialed neurologists: now → next 2 hours")
        coverage_table()

        eyebrow("Decide")
        st.subheader("Split shift: who can be called in")
        if not A["gap_hospitals"]:
            st.caption("No uncovered short hospitals.")
        else:
            st.caption("Short hospitals: " + ", ".join(HOSP[h] for h in A["gap_hospitals"]))
        if len(elig):
            rule_names = list(elig.iloc[0]["rules"].keys())
            tbl = pd.DataFrame([{
                "Doctor": f"{r['name']} ({r['physician_id']})",
                "Eligible": f"✅ #{int(r['rank'])}" if r["eligible"] else ("⏳ offer open" if r["offer_open"] else "❌"),
                **{k: ("✅ " if r["rules"][k][0] else "❌ ") + r["rules"][k][1] for k in rule_names},
            } for _, r in elig.iterrows()])
            st.dataframe(tbl, hide_index=True, width="stretch")
            if A["gap_hospitals"] and not (elig["eligible"].any() or elig["offer_open"].any()):
                st.warning("Nobody passes every rule → escalate to the clinical lead (never bend a rule).")
        if store.accepted_shifts:
            for s in store.accepted_shifts:
                st.success(f"✅ {PNAME[s['physician_id']]} joins {s['start']:%H:%M}–{s['end']:%H:%M}, "
                           f"routable only at {', '.join(HOSP[h] for h in s['hospitals'])}")

        t = A["trap"]
        if t:
            st.subheader("Split-shift trap: keeping the new doctor from being swamped")
            j = PNAME[A["joiner"]["physician_id"]]
            m1, m2 = st.columns(2)
            m1.metric(f"Default routing: {j}", f"{t['joiner_unrestricted']} of {t['total_calls']} calls",
                      f"{len(t['joiner_unrestricted_hospitals'])} hospital(s)", delta_color="off")
            m2.metric("Our plan: gap hospitals only + seeded count",
                      f"{t['joiner_restricted']} of {t['total_calls']} calls",
                      f"{len(t['joiner_restricted_hospitals'])} hospital(s)", delta_color="off")
            st.caption(f"Simulated next {t['total_calls']} calls (baseline demand, surge rate where surging). "
                       f"Default: {j} starts at 0 consults, so routing rule 4 sends them calls from "
                       f"{', '.join(HOSP[h] for h in t['joiner_unrestricted_hospitals'])}. "
                       f"Our plan limits them to the short hospitals and seeds their count at the team median "
                       f"({t['seed']}), so they share the load instead of absorbing it.")
            both = t["unrestricted"][["physician_id", "calls"]].merge(
                t["restricted"][["physician_id", "calls"]], on="physician_id", suffixes=(" (default)", " (our plan)"))
            both.insert(1, "Doctor", both["physician_id"].map(PNAME))
            st.dataframe(both.drop(columns="physician_id"), hide_index=True, width="stretch")

    with st.expander("Doctor activity (derived from signals, not self-reported)"):
        ph = state["physicians"].copy()
        ph.insert(1, "name", ph["physician_id"].map(PNAME))
        ph["risk_flags"] = ph["risk_flags"].apply(lambda f: "; ".join(f))
        st.dataframe(ph[["physician_id", "name", "shift_et", "derived_state", "self_reported_status",
                         "desktop_agent", "open_consults", "consults_this_shift", "minutes_to_shift_end",
                         "risk_flags"]], hide_index=True, width="stretch")
    with st.expander("Open consults: lifecycle and time in stage"):
        cs = state["consults"].copy()
        cs["hospital"] = cs["hospital_id"].map(HOSP)
        cs["stage_entered_at"] = cs["stage_entered_at"].dt.strftime("%H:%M:%S")
        st.dataframe(cs[["consult_id", "hospital", "consult_type", "physician_id", "current_stage",
                         "stage_entered_at", "minutes_in_stage", "minutes_since_received", "routing_failures"]],
                     hide_index=True, width="stretch")
    q = state["quality"]
    st.caption(f"Data handling: {len(q['duplicates'])} duplicate event(s) removed "
               f"({', '.join(q['duplicates']['event_id'])}); {len(q['late'])} late event(s) placed by event_time "
               f"({', '.join(q['late']['event_id'])}).")

with tab_lead:
    eyebrow("Clinical lead on duty")
    st.subheader("What puts stroke care at risk")
    risk = [p for p in problems if p.get("consult_type") == "Stroke" and p["severity"] == "critical"] + \
           [p for p in problems if p["kind"] == "gap"]
    for p in risk:
        st.markdown(f"- {SEV[p['severity']]} **{p['title']}**: {p['why'][0]}")

    eyebrow("Act")
    st.subheader("Waiting for your decision")
    pend = store.pending_approvals()
    if not pend:
        st.success("Nothing waiting for approval.")
    for o in pend:
        with st.container(border=True):
            backup = o["status"] == "pending_backup"
            if backup:
                st.warning("The clinical lead did not respond within 5 min, so this was escalated to the backup approver "
                           "(leadership chain).")
            st.markdown(f"**{o['id']} · {'Split-shift offer' if o['kind'] == 'split_shift' else 'Escalation'}**: {o['title']}")
            if o["kind"] == "split_shift":
                st.markdown(f"**Doctor:** {o['physician_name']} · **Hours:** {o['start']:%H:%M}–{o['end']:%H:%M} ET "
                            f"· **Online by:** {o['online_at']:%H:%M} · **Channel:** {o['channel']}")
                st.markdown("**Message the doctor will receive (no patient details):**")
                st.markdown(f'<div class="sv-msg">{html.escape(o["message"])}</div>', unsafe_allow_html=True)
            st.markdown("**Rationale:**\n" + "\n".join(f"- {r}" for r in o["rationale"]))
            if o["kind"] == "split_shift":
                with st.expander("Everyone considered, and why each was or wasn't eligible"):
                    st.dataframe(pd.DataFrame([{"Doctor": PNAME[c["physician_id"]],
                                                "Eligible": "✅" if c["eligible"] else "❌",
                                                "Failed rules": "; ".join(c["failed"]) or "—"}
                                               for c in o["considered"]]), hide_index=True, width="stretch")
            who = "Backup approver" if backup else "Clinical lead"
            b1, b2, _ = st.columns([1, 1, 3])
            if b1.button(f"Approve ({who})", key=f"ap_{o['id']}", type="primary"):
                store.approve(o["id"], now, who); st.rerun()
            if b2.button("Reject", key=f"rj_{o['id']}"):
                store.reject(o["id"], now, who); st.rerun()

    done = [o for o in store.proposals.values() if o not in pend]
    if done:
        st.subheader("Decided")
        st.dataframe(pd.DataFrame([{"ID": o["id"], "What": o["title"], "Status": o["status"].replace("_", " "),
                                    "Doctor": o.get("physician_name", "")} for o in done]),
                     hide_index=True, width="stretch")

with tab_phone:
    offers = store.sent_offers()
    choices = [o["physician_id"] for o in offers] + [p for p in ref["off_shift"]["physician_id"]
                                                     if p not in {o["physician_id"] for o in offers}]
    pid = st.selectbox("View the phone of", choices, format_func=lambda p: f"{PNAME[p]} ({p})")
    _, phone, _ = st.columns([1, 1.1, 1])
    with phone:
        with st.container(border=True, key="phone"):
            st.markdown(f'<div class="sv-phone-top"><span>{now:%H:%M}</span><span>Sevaro Synapse</span></div>'
                        f'<div class="sv-phone-hello">Good evening, {PNAME[pid]}</div>',
                        unsafe_allow_html=True)
            mine = next((o for o in offers if o["physician_id"] == pid), None)
            if mine:
                left_min = max(0, int((mine["expires_at"] - now).total_seconds() // 60))
                st.markdown("#### New split-shift offer")
                st.markdown(f"**Hospitals:** {', '.join(HOSP[h] for h in mine['hospitals'])}")
                st.markdown(f"**Hours:** {mine['start']:%H:%M} – {mine['end']:%H:%M} ET")
                st.markdown(f"**Start:** be online by {mine['online_at']:%H:%M}")
                st.markdown("**Why you:** credentialed at these hospitals, opted in, rested.")
                st.caption(f"Approved by {mine.get('approved_by', 'clinical lead')} · expires in {left_min} min")
                a, d = st.columns(2)
                if a.button("Accept", key=f"acc_{mine['id']}", type="primary", width="stretch"):
                    st.session_state.phone_reply[pid] = store.respond(mine["id"], True, now); st.rerun()
                if d.button("Decline", key=f"dec_{mine['id']}", width="stretch"):
                    st.session_state.phone_reply[pid] = store.respond(mine["id"], False, now); st.rerun()
            else:
                st.markdown(st.session_state.phone_reply.get(pid, "No offers right now."))

with tab_log:
    eyebrow("Record")
    st.subheader("Audit log (append-only)")
    st.caption("Every offer: who was considered, who was excluded and why, who approved, and the outcome. "
               "Machine-readable so it can trigger paging/messaging and support process review.")
    log = store.log_frame()
    st.dataframe(log, hide_index=True, width="stretch", column_config={
        "sim_time": st.column_config.TextColumn("Scenario time", width="small"),
        "logged_at": st.column_config.TextColumn("Logged at", width="small"),
        "actor": st.column_config.TextColumn("Actor", width="small"),
        "action": st.column_config.TextColumn("Action", width="medium"),
        "proposal_id": st.column_config.TextColumn("Offer / escalation", width="small"),
        "kind": st.column_config.TextColumn("Type", width="small"),
        "physician_id": st.column_config.TextColumn("Neurologist", width="small"),
        "hospitals": st.column_config.TextColumn("Hospitals", width="small"),
        "details": st.column_config.TextColumn("Details (JSON)", width="large"),
    })
    st.download_button("Download log (CSV)", log.to_csv(index=False), "offer_audit_log.csv", "text/csv")
