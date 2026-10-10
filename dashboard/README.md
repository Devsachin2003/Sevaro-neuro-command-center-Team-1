# Sevaro Operations Command Center: Detect → Decide → Act

Rutgers Health Hack 2026, Challenge 2 (team sevaro-team-01).

## Run

```bash
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
(cd dashboard && python check_snapshot.py)
(cd dashboard && python test_e2e.py)
```

Run commands from the repository root; the two validation commands above temporarily change into `dashboard/` so relative test fixtures resolve correctly. The synthetic challenge data is in `dashboard/data/`; set `SEVARO_DATA_DIR` to use another data package.

## Streamlit Community Cloud deployment

Use these exact settings:

- Repository: `Devsachin2003/Sevaro-neuro-command-center-Team-1`
- Branch: `main`
- Main file path: `dashboard/app.py`

Dependencies are in `dashboard/requirements.txt` and app theme config is loaded from repository-level `.streamlit/config.toml`.

## Architecture (build order)

```
CSV events ─► [1] loader ─► [2] replay ─┬─► [3] coverage ─┐
                                         ├─► [4] detect ───┼─► [6] decide ─► [7] analyze()
                                         └─► [5] routing sim┘                     │
                     [8] action store (offers, timers, audit log) ◄──────────────┘
                     accepted shifts / reassignments feed back into [7]
                                         │
            [9] Streamlit app: [10] Coordinator · [11] Clinical lead · [12] Doctor phone · [13] Audit log
            [14] demo/judge_test_events.csv + test_e2e.py
```

| # | File | Role |
|---|---|---|
| 1 | `engine/loader.py` | Load and validate CSVs, normalise times to ET |
| 2 | `engine/replay.py` | Dedupe, order by `event_time`, keep doctor state and consult state separate, derive availability |
| 3 | `engine/coverage.py` | Credentialed doctors vs requirement per hospital, now to +2 h, surge +1 |
| 4 | `engine/detect.py` | Ranked problems with what, where and why |
| 5 | `engine/routing_sim.py` | Models routing; shows the split-shift trap and our fix |
| 6 | `engine/decide.py` | Fix per problem, approver, RACI, eligibility table (doctor × rule) |
| 7 | `engine/pipeline.py` | `analyze()`: stateless; new data means call it again, no restart |
| 8 | `engine/act.py` | Offer state machine, approval timeout, 10-min expiry, first accept wins, audit log |
| 9–13 | `app.py` | Dashboard |

Design decisions and mentor answers: [DECISIONS.md](DECISIONS.md).

## Demo script (about 4 min)

1. **Coordinator tab.** "Act on this first": stroke consult CON-2105 at Oak Valley, past its 10-min limit, routing failed 3×, nobody credentialed is free → escalate. Point to the reason given for each fix.
2. **Coverage grid.** Suncrest (surge, 5 consults vs p90 of 1) and Northfield go short at 20:00 when shifts end.
3. **Split-shift table.** All 6 off-shift doctors × 7 rules; only Dr. Okafor (PHY-009) passes, and each of the others fails one rule.
4. **Split-shift trap.** Default routing: 10 of 20 calls from 3 hospitals. Our plan: 5 calls, only at the gap hospital.
5. Propose the offer → **Clinical lead tab**: message preview (no patient details) and everyone considered → Approve.
6. **Doctor phone tab** → Accept → back to Coordinator: "Hospitals short" drops to 0 and the grid turns from red to clear.
7. **Audit log tab** → download CSV.
8. **Live test.** Drop `demo/judge_test_events.csv` (or the judges' file) into the sidebar. A new Suncrest stroke consult appears and the list re-ranks. The duplicate and late events are handled, with no restart.

Extra branches to show if asked: click **Decline** (no one else qualifies → escalate); leave an approval for 5 min with **+5 min** (goes to the backup approver); leave an offer for 10 min (expires).
