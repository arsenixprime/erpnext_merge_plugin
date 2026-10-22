# Upstreaming Notes

Plan for proposing the merge engine to `frappe/frappe` (develop branch).

> Licensing: this app is GPL-3.0; frappe core is MIT. GreenSight Agronomics
> holds the copyright, so the engine can be contributed to frappe under MIT
> when the PR is opened — no third-party GPL code is involved.

## What maps to a core PR

| This app | Upstream shape |
|---|---|
| `merge_tool/merge_engine.py` | `frappe/model/merge_doc.py` (or folded into `frappe/model/rename_doc.py`) — the engine has no app-specific imports beyond the registry lookup |
| Merge Tool Settings registry | an `is_mergable` flag on the DocType doctype (like `allow_rename`), checked where `get_mergable_doctypes()` is called today |
| `merge_tool/api.py` | whitelisted methods next to `frappe.model.rename_doc.update_document_title` |
| `mergable_doctypes` hook | unnecessary upstream (the DocType flag replaces it) |
| `on_doc_merged` hook | a standard doc event (`doc_events`/`on_merge`) fired from `rename_doc(merge=True)` so *all* merges (including bulk rename CSV merges) get it |
| Merge dialog + list view action | core list view bulk action, gated on `is_mergable` — replaces this app's `ListView.prototype.set_actions_menu_items` wrapper with a first-class menu entry |
| `CHILD_DEDUP_KEYS` | move to DocType meta — e.g. a "merge dedup fields" property on child doctypes — instead of a hardcoded map |

## What must change for core

1. **Registry → meta flag.** Replace `get_mergable_doctypes()` (settings
   child table + hook) with `frappe.get_meta(doctype).is_mergable`. Engine
   call sites are already isolated to `_validate_pair`.
2. **Dedup keys → meta.** The `CHILD_DEDUP_KEYS` map is the only
   doctype-specific knowledge in the engine. Upstream it should be a child
   DocType property so Contact Email declares its own key.
3. **`on_doc_merged` → doc event.** Fire from inside `rename_doc` so the
   event also covers merges that don't go through this engine.
4. **Translatable strings/tests** already follow core conventions
   (`frappe._`, FrappeTestCase, tabs); tests would move to
   `frappe/tests/test_merge_doc.py`. The supplier/PO test is
   ERPNext-specific and would stay behind or move to ERPNext.
5. **Submittable support** (out of scope for v1) is the obvious follow-up
   question reviewers will ask: the engine currently refuses
   `is_submittable` doctypes; core may want merge-on-cancelled or
   amend-chain awareness instead of a flat refusal.

## Verified v15 behaviors the engine relies on

(Re-verify on develop before opening the PR.)

- `rename_doc(merge=True)` repoints Link fields (incl. custom fields and
  property setters), dynamic links, attachments (`File.attached_to_name`),
  Versions (`Version.docname`), Energy Point Logs and assignments, then
  calls `frappe.delete_doc(source)` and adds a "merged X into Y" comment.
  It does **not** move child-table rows of the source (that path,
  `rename_parent_and_child`, only runs for plain renames) — which is why
  this engine unions child rows *before* calling it.
- `validate_rename` requires the target to exist, write permission on both
  docs, and `meta.allow_rename`.
- Contact refuses to save with two primary emails/phones
  (`Contact.set_primary_email` / `set_primary`), so primary-flag
  reconciliation must happen before `target.save()`.
- `User.username` uniqueness is enforced by the controller (silently
  blanked when taken), not only by the DB index — meta `unique` flags and
  DB indexes are not the whole uniqueness story for every doctype.
- **Core bug found while testing:** `ToDo.update_in_reference` writes
  `_assign = ""` (empty string, not NULL) on the referenced document when
  no open assigned ToDos remain, and `rename_doc`'s `update_assignments`
  crashes on it (`frappe.parse_json("")` → JSONDecodeError). Stock v15
  rename-merge is broken for any document a reference-only ToDo has
  touched. This engine normalizes `_assign` before merging
  (`_normalize_assign`); a core PR should fix either writer or reader
  (likely both: write `null`, and make `update_assignments` tolerate
  empty strings).

## v16 differences (checked against frappe `version-16`, 16.53.0)

- `frappe.get_all` / `get_list` now go through the query builder and reject
  SQL functions given as strings in `fields` (`"count(distinct parent) as
  docs"` throws). The impact counter builds that query with `frappe.qb`
  instead, which works on v15 and v16 alike.
- `rename_doc` no longer repoints Energy Point Logs (energy points left
  core), casts autoincrement names to int, and publishes a `doc_rename`
  realtime event to open forms of the old name. Nothing the engine
  depends on changed.
- The `_assign = ""` bug above is still present in v16
  (`ToDo.update_in_reference` and `update_assignments` are unchanged), so
  `_normalize_assign` stays.
- `ListView.set_actions_menu_items` and `page.add_actions_menu_item` are
  unchanged from v15; Bootstrap is still 4.6.2, so the dropdown
  `show.bs.dropdown` hook in `list_view_merge.js` still applies.
- Tests use `frappe.tests.IntegrationTestCase`; `FrappeTestCase` is a
  deprecated compat shim in v16, and `IntegrationTestCase` does not exist
  in v15, so the test base class differs between the two branches.
