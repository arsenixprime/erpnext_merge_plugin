# merge_tool — project conventions

Custom Frappe app adding interactive record merging to Frappe/ERPNext **v15**.
Standalone app, no core patches. The server-side merge engine is written to
frappe-core standards because it is intended for a future upstream PR to
`frappe/frappe`.

## Architecture (per project brief)

- **Merge anchor:** `frappe.model.rename_doc.rename_doc(merge=True)` does all
  link repointing (Link, Dynamic Link, child tables, Singles) and deletes the
  source. We do NOT reimplement repointing.
- **Engine adds:** field-level conflict resolution, child-table union with
  dedup, impact preview, post-merge cleanup, audit trail.
- **Transaction order:** validate → write resolved fields on survivor → union
  child rows → `rename_doc(merge=True)` → cleanup hooks → audit comment +
  JSON snapshot of source. No `frappe.db.commit()` inside the flow; any
  failure rolls back everything.
- **Mergability registry:** Single doctype "Merge Tool Settings" (child table
  of enabled doctypes; seeded with Contact, Supplier, Customer, Address, Lead)
  plus a `mergable_doctypes` hook for other apps.
- **v1 guardrails:** docstatus 0 only; write+delete permission + doctype must
  allow rename; pre-flight unique-constraint check.
- Keep `merge_engine.py` (engine) cleanly separated from `api.py`
  (whitelisted endpoints) and from any UI glue.
- **Verify frappe internals against the v15 source in the bench**
  (`apps/frappe/...`), not from memory.

## Environment

Dockerized frappe_docker dev setup — full bring-up in `docs/DEV_SETUP.md`.
Bench path on host: `frappe_docker/development/frappe-bench/`; inside the
container: `/workspace/development/frappe-bench`. Site: `dev.localhost`
(Administrator/admin) on <http://dev.localhost:8000>.

```bash
# shell into the bench
docker exec -it -w /workspace/development/frappe-bench devcontainer-frappe-1 bash

# run this app's tests
bench --site dev.localhost run-tests --app merge_tool

# console / migrate
bench --site dev.localhost console
bench --site dev.localhost migrate
```

Inside the container, run `nvm use v20.20.2` before anything that builds JS
assets (image default node 24 is not v15-compatible).

## Git rules (strict)

- Branches follow the frappe convention: `version-15`, `version-16` are the
  per-major release branches; `develop` is the integration branch for the
  newest major (currently v16) and `version-16` is cut from it. Fixes that
  apply to v15 are cherry-picked onto `version-15`. Each branch pins its
  frappe range in `pyproject.toml` `[tool.bench.frappe-dependencies]`.
- Day-to-day work on `develop`; user decides when to update release branches.
- Conventional commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- **Never commit or push without explicit user approval** — present diff
  summary + proposed message first. No force-push, no history rewrites, no
  secrets/site config in commits.
- Only this app repo is committed — never the bench or frappe_docker clone.

## Core-code patches

One JS prototype wrapper (no Python patches):

- **`frappe.views.ListView.prototype.set_actions_menu_items`** is wrapped in
  `merge_tool/public/js/list_view_merge.js`. The original method runs
  unchanged; afterwards a "Merge" item is appended via the public
  `page.add_actions_menu_item` API for doctypes in the merge registry,
  enabled only when exactly two rows are checked. ReportView inherits the
  method, covering report view too. Reason: v15 has no extension point for
  doctype-generic bulk actions (`get_actions_menu_items` is a closed list;
  `frappe.listview_settings` is per-doctype and collides with erpnext's own
  settings). On framework upgrades, re-check
  `frappe/public/js/frappe/list/list_view.js::set_actions_menu_items` (v15:
  builds the menu once in `setup_page_head`).

# Layout

- `merge_tool/merge_engine.py` — engine (upstream candidate; see
  docs/UPSTREAM_NOTES.md). `merge_tool/api.py` — whitelisted wrappers.
- `merge_tool/merge_tool/doctype/merge_tool_settings/` (+ child doctype) —
  the mergability registry, seeded by `merge_tool/install.py`.
- `merge_tool/public/js/merge_dialog.js` — the merge dialog;
  `list_view_merge.js` — list view action; bundled by
  `merge_tool.bundle.js` via `app_include_js`.
- Tests: `merge_tool/tests/test_merge_engine.py` (13 tests; suppliers class
  creates Company/Item/Warehouse-Type fixtures for bare sites).
