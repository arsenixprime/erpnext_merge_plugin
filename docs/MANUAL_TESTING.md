# Manual Testing Walkthrough

Browser test script for the merge UI. Assumes the dev environment from
`DEV_SETUP.md`: <http://dev.localhost:8000>, Administrator / `admin`
(v16 bench: <http://dev.localhost:8016>).

> **v16 status:** this walkthrough has **not yet been run by a human on
> v16.** Sections 2–5 were run on v16 by a scripted headless browser
> (Playwright) only; sections 6–7 and the comment carry-over check in
> section 4 have not been run on v16 at all.

## 1. Setup test data (2 minutes)

1. Go to **Contact List** → New. Create:
   - Contact A: first name `Alice`, designation `Engineer`, email
     `shared@example.com` (primary) and `alice@example.com`, phone
     `+1-555-0100` (primary).
   - Contact B: first name `Alicia`, designation `Manager`, email
     `shared@example.com` (primary) and `alicia@example.com`.
2. Open Contact A, add a comment ("merge test comment") and attach any file.

## 2. Action visibility

- [ ] In **Contact List**, select **one** row → Actions menu: **Merge** is
      greyed out (v15 also shows the tooltip "Select exactly two records to
      merge"; v16's menu does not render item tooltips).
- [ ] Select **two** rows → **Merge** is active.
- [ ] Select **three** rows → greyed out again.
- [ ] In **ToDo List** (not an enabled doctype), select two rows → no Merge
      entry at all.
- [ ] Switch Contact List to **Report** view, select two rows → Merge works
      there too.

## 3. The merge dialog

Select Contacts A and B → Actions → Merge.

- [ ] Two survivor cards show name, creation date, and linked-document count;
      the default survivor is the most-linked (or older) record. Clicking the
      other card flips survivor/deleted labels and re-renders the tables.
- [ ] Impact section lists linked doctypes with per-record counts.
- [ ] Conflict table shows `First Name` and `Designation` with both values;
      survivor's value is pre-selected.
- [ ] `Designation` (text) offers **Concatenate**; checking it disables the
      radios and reveals a separator input.
- [ ] "No conflict (N fields)" section is collapsed; expanding shows
      identical/empty fields with their automatic resolution.
- [ ] Child tables section lists Emails / Phones / Links with strategy
      dropdowns (default Union) and an expandable preview of resulting rows —
      `shared@example.com` appears **once** in the union preview.

## 4. Execute

- [ ] Pick `Alice` for first name, Concatenate designations with separator
      ` / `, leave child tables on Union → **Merge**.
- [ ] Confirmation restates: "A will be merged into B… N linked document(s)
      will be repointed… A will be deleted… cannot be undone."
- [ ] After confirming you land on the survivor's form.

Verify on the survivor:

- [ ] First name `Alice`, designation `Manager / Engineer` (order:
      survivor's value first).
- [ ] Emails: `shared@example.com`, `alice@example.com`, `alicia@example.com`
      — exactly one primary (the survivor's original).
- [ ] Phone `+1-555-0100` present.
- [ ] The comment and the attached file from Contact A are on the survivor.
- [ ] Two merge comments: frappe's "merged A into B" and the Merge Tool audit
      comment with expandable JSON snapshot of A.
- [ ] Contact A is gone from the list.

## 5. Suppliers (link repointing)

1. Create Suppliers S1 and S2 (any group). Create a draft **Purchase Order**
   for S1. Create an **Address**, linking it to both S1 and S2 in its
   Links table.
2. Merge S1 into S2 (S2 survivor).

- [ ] The PO now shows supplier S2.
- [ ] The Address has exactly **one** Links row (S2) — the duplicate from
      repointing was collapsed.
- [ ] S1 is deleted.

## 6. Failure paths

- [ ] As a user without delete permission on Contact, the merge errors with
      a permission message and neither contact changes.
- [ ] Try merging via API with a missing resolution
      (`merge_tool.api.merge_docs` with empty `resolutions` on conflicting
      records) → error names the unresolved fields, nothing changes.

## 7. Settings

- [ ] **Merge Tool Settings**: remove Contact from Enabled DocTypes → Merge
      disappears from Contact List (after reload). Re-add it → it returns.
- [ ] Adding a single/child doctype to the table is rejected on save.
