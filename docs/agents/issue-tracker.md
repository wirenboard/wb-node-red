# Issue tracker

**Where issues live:** local markdown files under `doc/issues/`, one file per
issue (`NN-kebab-title.md`), indexed by `doc/issues/README.md`.

There is **no remote tracker wired up yet**. The eventual target named in the
PRD is GitHub **`wirenboard/wb-docker`** with a `ready-for-agent` label; until
that repo/label exist, work locally in markdown.

## How skills should use it

- **Create / break down issues** (`to-issues`, `to-prd`): write new markdown
  files under `doc/issues/` following the existing template (sections: Parent,
  What to build, Acceptance criteria, Blocked by) and add a row to
  `doc/issues/README.md`. Number them in dependency order.
- **Read issues:** parse the markdown body; acceptance criteria are `- [ ]`
  checkboxes, dependencies are listed under "Blocked by", and the
  AFK/HITL type + triage marker are in the header (see triage-labels.md).
- **Do NOT** call `gh issue create` / GitLab `glab` — there is no remote.

## When the GitHub repo exists

Switch this file to "GitHub `wirenboard/wb-docker` via the `gh` CLI", create the
`ready-for-agent` label, and migrate `doc/issues/*` into GitHub Issues (the
markdown files map 1:1 to `gh issue create` calls).
