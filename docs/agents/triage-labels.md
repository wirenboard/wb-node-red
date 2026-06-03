# Triage labels

There is no label system (no remote tracker yet — see issue-tracker.md), so the
five canonical triage roles are recorded as **plain-text markers inside each
markdown issue file**, not as tracker labels.

| Canonical role | How it's recorded in `doc/issues/*.md` |
|----------------|----------------------------------------|
| `needs-triage` | (default for a freshly-written issue with no marker line) |
| `needs-info` | `Labels: needs-info` in the header |
| `ready-for-agent` | `Labels: ready-for-agent` in the header — AFK-ready, an agent can pick it up with no human context |
| `ready-for-human` | `Labels: ready-for-human` in the header |
| `wontfix` | `Labels: wontfix` in the header |

Independently, every issue header also carries a **`Type: AFK | HITL`** line:

- **AFK** — implementable and self-verifiable with no human decision.
- **HITL** — needs a human decision, a real controller (candidate `aat3d5fw`),
  or external infra (WB registry) to verify. HITL issues are still
  `ready-for-agent` for the *implementable* part; their verification tail is
  logged for a human (see the afk-pipeline audit convention).

## How skills should use it

- `triage`: move an issue through the state machine by editing its `Labels:`
  header line in the markdown file; default an unmarked issue to `needs-triage`.
- `to-issues` / `afk-pipeline`: emit issues with `Labels: ready-for-agent` once
  they are fully specified.
