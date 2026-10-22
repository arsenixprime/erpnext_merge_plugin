# Merge Tool

Interactive record merging for Frappe/ERPNext **v15**.

Select two records in any list view, pick which one survives, resolve
conflicting fields side by side, and merge — every reference (links, dynamic
links, communications, comments, files, child tables) is repointed to the
survivor and the duplicate is deleted, all in a single transaction that rolls
back fully on any failure. Built for cleaning up CRM duplicates (Contacts,
Suppliers, Customers, Addresses, Leads) but doctype-generic.

**Highlights**

- **Merge** action in the list/report view Actions menu, active when exactly
  two rows are selected.
- Pre-merge **impact report**: how many documents link to each record.
- **Conflict table**: pick the source or target value per field, or
  concatenate text fields; identical/empty fields auto-resolve.
- **Child tables**: union with key-based deduplication (default) or keep one
  side; at most one `is_primary*` flag survives.
- **Audit trail**: comment on the survivor with who/what/when and a JSON
  snapshot of the deleted record, so merges are recoverable by hand.
- Guardrails: draft documents only, write+delete permissions enforced,
  unique-constraint collisions detected before anything is written.

## Branches

Following the frappe/erpnext convention, each supported major version has a
`version-NN` branch; `develop` is the integration branch for the newest one.

| Frappe/ERPNext | Branch | Status |
|---|---|---|
| v15 | `version-15` | Supported (original target) |
| v16 | `version-16` | **Not yet tested by humans.** Automated tests pass and the walkthrough was run by a scripted headless browser only |

## Installation

On a Frappe/ERPNext **version-15** bench:

```bash
bench get-app --branch version-15 https://github.com/<owner>/merge_tool
bench --site <your-site> install-app merge_tool
```

Installing enables merging for Contact, Supplier, Customer, Address and Lead
(those present on the site). Manage the list in **Merge Tool Settings**.

## Usage

1. Open the list view of an enabled doctype and select **exactly two** rows.
2. **Actions → Merge.**
3. Choose the survivor, resolve any conflicting fields, review the child
   table previews, and confirm.

The merge cannot be undone from the UI — the confirmation step tells you
exactly what will be repointed and deleted before you commit.

## Extending from other apps

```python
# hooks.py
mergable_doctypes = ["My DocType"]            # enable doctypes programmatically
on_doc_merged = ["my_app.utils.after_merge"]  # fn(doctype, source_name, target_doc),
                                              # runs inside the merge transaction
```

Whitelisted API, usable from scripts: `merge_tool.api.get_merge_impact`,
`get_merge_preview`, `merge_docs(doctype, source, target, resolutions)`.

## Known limitations

- Submitted/submittable documents cannot be merged (v1 scope).
- Record names stored as plain text (saved filters, scripts) are not
  repointed — same limitation as frappe's own rename/merge.
- Multi-column unique indexes are not pre-flight checked.
- Fields the merging user cannot read (field-level / permlevel permissions)
  and Password fields take no part in the merge: they are hidden from the
  preview and audit snapshot, and the survivor keeps its own values.

## Security notes

- **Impact counts ignore per-doctype read permissions.** The pre-merge
  impact report counts referencing documents across all doctypes so the
  merge scope is truthful — a user who can read the two records being
  merged can therefore learn *how many* (never *which*, nor any content)
  documents of other doctypes link to them, even without read permission on
  those doctypes. A real but minor disclosure; if that matters for your
  permission model, restrict who can merge.
- Merging repoints references on documents the merging user may not
  otherwise be able to write (inherent to frappe's rename-merge, which this
  app builds on). Treat **delete permission on the enabled doctypes** as
  the gate for who may merge.

## Development

`docs/DEV_SETUP.md` — dockerized v15 dev bench. `docs/MANUAL_TESTING.md` —
browser walkthrough. `docs/UPSTREAM_NOTES.md` — plan for upstreaming the
engine to frappe core.

```bash
bench --site <site> set-config allow_tests true
bench --site <site> run-tests --app merge_tool
```

## License

GPL-3.0. See `license.txt`.
