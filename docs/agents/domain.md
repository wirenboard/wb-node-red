# Domain docs

**Layout: single-context.** One domain, one glossary, one decision log.

| What | Where | Read it for |
|------|-------|-------------|
| Domain glossary / ubiquitous language | [CONTEXT.md](../../CONTEXT.md) | term vocabulary for code, tests, issues, commits |
| Foundational decisions (15) | [doc/wb-docker-apps-design.md](../../doc/wb-docker-apps-design.md) | why the architecture is the way it is |
| Product requirements | [doc/wb-docker-apps-prd.md](../../doc/wb-docker-apps-prd.md) | user stories, module decomposition, scope |
| Architecture Decision Records | [docs/adr/](../adr/) | decisions made *during/after* implementation |

## Consumer rules

- **`tdd`, `diagnose`:** take test names and interface vocabulary from
  CONTEXT.md so they match the project's language.
- **`improve-codebase-architecture`:** read CONTEXT.md for the domain and
  `docs/adr/` (plus the design doc) before proposing changes; respect existing
  decisions or supersede them with a new ADR.
- **`to-issues`, `to-prd`:** title and describe issues in CONTEXT.md vocabulary.
- The design doc (`doc/wb-docker-apps-design.md`) is the **canonical record of
  the 15 foundational decisions**; `docs/adr/` captures decisions taken later
  (e.g. during the AFK implementation) that aren't in the design doc.
