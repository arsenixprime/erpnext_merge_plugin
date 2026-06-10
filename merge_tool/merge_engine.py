# Copyright (c) 2026, GreenSight Agronomics and contributors
# License: GNU General Public License v3. See license.txt
"""Doctype-generic record merge engine.

Merges a *source* document into a *target* (survivor) document of the same
doctype. The heavy lifting of repointing every reference (Link fields,
Dynamic Link fields, child tables, Singles, attachments, versions,
assignments) is delegated to frappe's rename machinery via
``frappe.model.rename_doc.rename_doc(merge=True)``. This engine adds what
rename-merge lacks:

- field-level conflict resolution (pick source/target, or concatenate text),
- child-table union with key-based deduplication and primary-flag
  reconciliation,
- a pre-merge impact report and field-by-field preview,
- pre-flight unique-constraint checks,
- post-merge cleanup of duplicate dynamic-link child rows created by
  repointing,
- an audit comment with a JSON snapshot of the source document.

The whole merge runs in the caller's transaction: nothing here commits, so
any failure rolls back the entire merge.
"""

import json

import frappe
from frappe import _
from frappe.model import child_table_fields, default_fields, no_value_fields, optional_fields, table_fields
from frappe.model.dynamic_links import get_dynamic_link_map
from frappe.model.rename_doc import get_link_fields, rename_doc
from frappe.utils import cstr, now

# Fields that never take part in conflict resolution.
IGNORED_FIELDS = frozenset(default_fields) | frozenset(optional_fields) | {"naming_series", "amended_from"}

# Fieldtypes that may be concatenated instead of picking one side.
CONCATENABLE_FIELDTYPES = frozenset(("Data", "Small Text", "Text", "Long Text", "Text Editor"))

# Child doctypes with a well-known deduplication key. Any child doctype not
# listed here falls back to comparing all significant columns.
CHILD_DEDUP_KEYS = {
	"Dynamic Link": ("link_doctype", "link_name"),
	"Contact Email": ("email_id",),
	"Contact Phone": ("phone",),
}


def get_mergable_doctypes() -> list[str]:
	"""Return doctypes enabled for merging.

	Union of the rows in Merge Tool Settings and any doctypes registered by
	other apps through the ``mergable_doctypes`` hook.
	"""
	doctypes = []
	settings = frappe.get_cached_doc("Merge Tool Settings")
	doctypes.extend(row.document_type for row in settings.enabled_doctypes)
	for hooked in frappe.get_hooks("mergable_doctypes") or []:
		if hooked not in doctypes:
			doctypes.append(hooked)
	return doctypes


def get_merge_impact(doctype: str, source: str, target: str) -> dict:
	"""Count documents that reference each of the two records.

	Uses the same metadata as ``rename_doc`` (``get_link_fields`` and the
	dynamic link map), so the report reflects exactly what a merge would
	repoint. Child-table references are counted as their parent document's
	doctype (one Purchase Order with three matching rows counts once).

	Return ``{"source": {linked_doctype: count}, "target": {...}}``.
	"""
	_validate_pair(doctype, source, target, permtype="read")

	impact = {"source": {}, "target": {}}
	for side, name in (("source", source), ("target", target)):
		counts = impact[side]
		for df in get_link_fields(doctype):
			_count_references(counts, df.parent, df.fieldname, name, issingle=df.issingle, self_pair=(doctype, source, target))
		for df in get_dynamic_link_map().get(doctype, []):
			meta = frappe.get_meta(df.parent)
			if meta.is_virtual:
				continue
			if meta.issingle:
				values = frappe.db.get_singles_dict(df.parent)
				if values.get(df.options) == doctype and values.get(df.fieldname) == name:
					counts[df.parent] = counts.get(df.parent, 0) + 1
				continue
			_count_references(
				counts,
				df.parent,
				df.fieldname,
				name,
				extra_filters={df.options: doctype},
				self_pair=(doctype, source, target),
			)
	return impact


def _count_references(counts, parent_doctype, fieldname, name, issingle=False, extra_filters=None, self_pair=None):
	"""Add reference counts of one linking docfield into ``counts``."""
	if issingle:
		if frappe.db.get_single_value(parent_doctype, fieldname) == name:
			counts[parent_doctype] = counts.get(parent_doctype, 0) + 1
		return

	meta = frappe.get_meta(parent_doctype)
	if meta.issingle or meta.is_virtual:
		return

	filters = {fieldname: name}
	if extra_filters:
		filters.update(extra_filters)

	if meta.istable:
		# count distinct parent documents, grouped by their doctype
		rows = frappe.get_all(
			parent_doctype,
			filters=filters,
			fields=["parenttype", "count(distinct parent) as docs"],
			group_by="parenttype",
			ignore_permissions=True,
		)
		merge_doctype, source, target = self_pair
		for row in rows:
			if not row.parenttype:
				continue
			docs = row.docs
			if row.parenttype == merge_doctype:
				# rows owned by the two records being merged are part of the
				# merge itself, not external impact
				docs = frappe.db.count(
					parent_doctype,
					{**filters, "parenttype": merge_doctype, "parent": ("not in", (source, target))},
				)
				if not docs:
					continue
			counts[row.parenttype] = counts.get(row.parenttype, 0) + docs
	else:
		count = frappe.db.count(parent_doctype, filters)
		if count:
			counts[parent_doctype] = counts.get(parent_doctype, 0) + count


def get_merge_preview(doctype: str, source: str, target: str) -> dict:
	"""Build a field-by-field diff between source and target.

	Return a dict with:

	- ``fields``: one entry per value-bearing docfield: fieldname, label,
	  fieldtype, both values, ``can_concat``, and ``status`` —
	  ``"conflict"`` (user must resolve), or ``"auto"`` with
	  ``auto_resolution`` explaining what happens without input
	  (``keep_target`` for identical/source-empty values, ``use_source``
	  when only the target is empty).
	- ``child_tables``: one entry per Table/Table MultiSelect field:
	  fieldname, label, child doctype, dedup key, both row sets and the
	  resulting union preview.
	"""
	_validate_pair(doctype, source, target, permtype="read")
	source_doc = frappe.get_doc(doctype, source)
	target_doc = frappe.get_doc(doctype, target)

	fields = []
	child_tables = []
	allowed_permlevels = _allowed_read_permlevels(target_doc)
	for df in frappe.get_meta(doctype).fields:
		if df.permlevel not in allowed_permlevels:
			# fields the user cannot read (field-level permissions) take no
			# part in the merge: hidden from the preview, the survivor keeps
			# its own values
			continue
		if df.fieldtype in table_fields:
			child_tables.append(_child_table_preview(df, source_doc, target_doc))
			continue
		if not _is_resolvable_field(df):
			continue
		source_value = source_doc.get(df.fieldname)
		target_value = target_doc.get(df.fieldname)
		entry = {
			"fieldname": df.fieldname,
			"label": _(df.label) if df.label else df.fieldname,
			"fieldtype": df.fieldtype,
			"source_value": source_value,
			"target_value": target_value,
			"can_concat": df.fieldtype in CONCATENABLE_FIELDTYPES,
		}
		if _values_equal(source_value, target_value) or _is_empty(source_value):
			entry["status"] = "auto"
			entry["auto_resolution"] = "keep_target"
		elif _is_empty(target_value):
			entry["status"] = "auto"
			entry["auto_resolution"] = "use_source"
		else:
			entry["status"] = "conflict"
		fields.append(entry)

	return {"doctype": doctype, "source": source, "target": target, "fields": fields, "child_tables": child_tables}


def merge_docs(doctype: str, source: str, target: str, resolutions: dict | None = None) -> dict:
	"""Merge ``source`` into ``target`` and delete ``source``.

	``resolutions`` maps fieldnames to a resolution:

	- regular field: ``{"use": "source"|"target"}`` or
	  ``{"concat": True, "separator": "\\n"}`` (text fieldtypes only;
	  separator falls back to the Merge Tool Settings default),
	- child table field: ``{"strategy": "union"|"target_only"|"source_only"}``
	  (default ``union``).

	Every field reported as a conflict by :func:`get_merge_preview` must
	have a resolution, otherwise the merge refuses to start.

	Runs in the ambient transaction — never commits, so an exception at any
	step rolls the whole merge back.
	"""
	resolutions = frappe.parse_json(resolutions) if resolutions else {}

	_validate_pair(doctype, source, target, permtype="write")
	_validate_merge_guardrails(doctype, source, target)

	source_doc = frappe.get_doc(doctype, source)
	target_doc = frappe.get_doc(doctype, target)
	preview = get_merge_preview(doctype, source, target)

	_validate_resolutions(preview, resolutions)
	resolved_values = _resolved_field_values(preview, resolutions, source_doc, target_doc)
	_check_unique_collisions(doctype, source, target, resolved_values, target_doc)

	snapshot = json.dumps(_snapshot_dict(source_doc), indent=1, default=str, sort_keys=True)

	# free up unique values held by the source before the target adopts them
	_release_source_unique_values(source_doc, resolved_values)

	for fieldname, value in resolved_values.items():
		target_doc.set(fieldname, value)

	for table in preview["child_tables"]:
		strategy = (resolutions.get(table["fieldname"]) or {}).get("strategy", "union")
		_apply_child_strategy(table, strategy, source_doc, target_doc)

	target_doc.save()

	_normalize_assign(doctype, source, target)
	rename_doc(doctype, source, target, merge=True, show_alert=False)

	_dedupe_repointed_dynamic_links(doctype, target)

	target_doc = frappe.get_doc(doctype, target)
	for method in frappe.get_hooks("on_doc_merged") or []:
		frappe.get_attr(method)(doctype=doctype, source_name=source, target_doc=target_doc)

	_add_audit_comment(target_doc, source, snapshot)

	return {"doctype": doctype, "source": source, "target": target}


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _validate_pair(doctype: str, source: str, target: str, permtype: str) -> None:
	"""Common checks for all three public calls."""
	if not frappe.db.exists("DocType", doctype):
		frappe.throw(_("DocType {0} does not exist").format(doctype))

	meta = frappe.get_meta(doctype)
	if meta.issingle or meta.istable or meta.is_virtual:
		frappe.throw(_("{0} documents cannot be merged").format(doctype))

	if source == target:
		frappe.throw(_("Source and target must be different documents"))

	if doctype not in get_mergable_doctypes():
		frappe.throw(
			_("Merging is not enabled for {0}. Enable it in Merge Tool Settings.").format(_(doctype))
		)

	for name in (source, target):
		if not frappe.db.exists(doctype, name):
			frappe.throw(_("{0} {1} does not exist").format(_(doctype), name))
		frappe.has_permission(doctype, permtype, doc=name, throw=True)


def _validate_merge_guardrails(doctype: str, source: str, target: str) -> None:
	"""Hard requirements before any write: permissions, rename support, docstatus."""
	meta = frappe.get_meta(doctype)
	if not meta.allow_rename:
		frappe.throw(
			_("{0} does not allow renaming, so its records cannot be merged").format(_(doctype))
		)

	frappe.has_permission(doctype, "write", doc=target, throw=True)
	frappe.has_permission(doctype, "write", doc=source, throw=True)
	if not frappe.has_permission(doctype, "delete", doc=source):
		frappe.throw(
			_("You need delete permission on {0} {1} to merge it").format(_(doctype), source),
			frappe.PermissionError,
		)

	if meta.is_submittable:
		frappe.throw(_("Submittable doctypes are not supported for merging yet"))

	for name in (source, target):
		if frappe.db.get_value(doctype, name, "docstatus") != 0:
			frappe.throw(_("{0} {1} is not in draft status and cannot be merged").format(_(doctype), name))


def _validate_resolutions(preview: dict, resolutions: dict) -> None:
	"""Reject missing or malformed resolutions before anything is written."""
	conflicts = {f["fieldname"]: f for f in preview["fields"] if f["status"] == "conflict"}
	missing = [f for f in conflicts if f not in resolutions]
	if missing:
		frappe.throw(
			_("No resolution provided for conflicting field(s): {0}").format(", ".join(missing))
		)

	field_info = {f["fieldname"]: f for f in preview["fields"]}
	table_names = {t["fieldname"] for t in preview["child_tables"]}
	for fieldname, resolution in resolutions.items():
		if not isinstance(resolution, dict):
			frappe.throw(_("Resolution for {0} must be an object").format(fieldname))
		if fieldname in table_names:
			if resolution.get("strategy", "union") not in ("union", "target_only", "source_only"):
				frappe.throw(_("Unknown child table strategy for {0}").format(fieldname))
			continue
		if fieldname not in field_info:
			frappe.throw(_("{0} is not a resolvable field").format(fieldname))
		if resolution.get("concat"):
			if not field_info[fieldname]["can_concat"]:
				frappe.throw(
					_("Field {0} of type {1} cannot be concatenated").format(
						fieldname, field_info[fieldname]["fieldtype"]
					)
				)
		elif resolution.get("use") not in ("source", "target"):
			frappe.throw(_('Resolution for {0} must be {{"use": "source"|"target"}} or {{"concat": true}}').format(fieldname))


def _resolved_field_values(preview: dict, resolutions: dict, source_doc, target_doc) -> dict:
	"""Compute the survivor's final value for every field that changes."""
	default_separator = (
		frappe.db.get_single_value("Merge Tool Settings", "default_concat_separator") or "\n"
	)
	values = {}
	for field in preview["fields"]:
		fieldname = field["fieldname"]
		resolution = resolutions.get(fieldname)
		if resolution:
			if resolution.get("concat"):
				separator = resolution.get("separator")
				if separator is None:
					separator = default_separator
				parts = [cstr(target_doc.get(fieldname)), cstr(source_doc.get(fieldname))]
				value = separator.join(p for p in parts if p)
			elif resolution["use"] == "source":
				value = source_doc.get(fieldname)
			else:
				continue  # explicit keep_target: nothing to change
		elif field["status"] == "auto" and field["auto_resolution"] == "use_source":
			value = source_doc.get(fieldname)
		else:
			continue
		if not _values_equal(value, target_doc.get(fieldname)):
			values[fieldname] = value
	return values


# ---------------------------------------------------------------------------
# unique constraints
# ---------------------------------------------------------------------------


def _get_unique_fieldnames(doctype: str) -> list[str]:
	"""Unique docfields plus single-column unique DB indexes.

	Multi-column unique indexes are not checked (none exist on standard
	mergable masters; revisit if needed).
	"""
	meta = frappe.get_meta(doctype)
	fieldnames = {df.fieldname for df in meta.fields if df.unique and _is_resolvable_field(df)}

	for index in frappe.db.sql(
		f"SHOW INDEX FROM `tab{doctype}` WHERE Non_unique = 0 AND Key_name != 'PRIMARY'",
		as_dict=True,
	):
		column = index.get("Column_name")
		if column and meta.has_field(column):
			fieldnames.add(column)
	return sorted(fieldnames)


def _check_unique_collisions(doctype, source, target, resolved_values, target_doc) -> None:
	"""Fail before writing anything if a post-merge unique value would collide
	with a document other than the two being merged."""
	for fieldname in _get_unique_fieldnames(doctype):
		value = resolved_values.get(fieldname, target_doc.get(fieldname))
		if _is_empty(value):
			continue
		collision = frappe.db.get_value(
			doctype, {fieldname: value, "name": ("not in", (source, target))}, "name"
		)
		if collision:
			frappe.throw(
				_(
					"Cannot merge: field {0} must be unique, but value {1} is already used by {2} {3}"
				).format(frappe.bold(fieldname), frappe.bold(value), _(doctype), frappe.bold(collision))
			)


def _release_source_unique_values(source_doc, resolved_values) -> None:
	"""Null unique fields on the source row when the survivor adopts their
	values, so the survivor's save does not trip the unique index. The source
	row is deleted by rename-merge a moment later."""
	for fieldname in _get_unique_fieldnames(source_doc.doctype):
		if fieldname not in resolved_values:
			continue
		if _values_equal(resolved_values[fieldname], source_doc.get(fieldname)):
			frappe.db.set_value(
				source_doc.doctype, source_doc.name, fieldname, None, update_modified=False
			)


# ---------------------------------------------------------------------------
# child tables
# ---------------------------------------------------------------------------


def _child_table_preview(df, source_doc, target_doc) -> dict:
	source_rows = [_significant_child_values(row) for row in source_doc.get(df.fieldname) or []]
	target_rows = [_significant_child_values(row) for row in target_doc.get(df.fieldname) or []]
	dedup_key = CHILD_DEDUP_KEYS.get(df.options)

	union_rows = list(target_rows)
	seen = {_child_row_key(row, df.options) for row in target_rows}
	for row in source_rows:
		if _child_row_key(row, df.options) not in seen:
			union_rows.append(row)

	return {
		"fieldname": df.fieldname,
		"label": _(df.label) if df.label else df.fieldname,
		"child_doctype": df.options,
		"dedup_key": list(dedup_key) if dedup_key else None,
		"source_rows": source_rows,
		"target_rows": target_rows,
		"union_preview": union_rows,
	}


def _apply_child_strategy(table: dict, strategy: str, source_doc, target_doc) -> None:
	"""Mutate the target doc's child table per the chosen strategy."""
	fieldname = table["fieldname"]
	child_doctype = table["child_doctype"]

	if strategy == "target_only":
		appended = []
	elif strategy == "source_only":
		target_doc.set(fieldname, [])
		appended = [
			target_doc.append(fieldname, _significant_child_values(row))
			for row in source_doc.get(fieldname) or []
		]
	else:  # union
		existing_keys = {
			_child_row_key(_significant_child_values(row), child_doctype)
			for row in target_doc.get(fieldname) or []
		}
		appended = []
		for row in source_doc.get(fieldname) or []:
			values = _significant_child_values(row)
			key = _child_row_key(values, child_doctype)
			if key in existing_keys:
				continue
			existing_keys.add(key)
			appended.append(target_doc.append(fieldname, values))

	_reconcile_primary_flags(target_doc, fieldname, appended)


def _significant_child_values(row) -> dict:
	"""A child row's values minus identity/meta columns, suitable for
	appending to another parent."""
	skip = set(default_fields) | set(child_table_fields) | set(optional_fields) | {"doctype"}
	# row may be a Document or a frappe._dict (whose attribute access never
	# raises, so check callability rather than presence)
	as_dict = getattr(row, "as_dict", None)
	values = as_dict() if callable(as_dict) else dict(row)
	return {k: v for k, v in values.items() if k not in skip and not k.startswith("_")}


def _child_row_key(values: dict, child_doctype: str) -> tuple:
	"""Deduplication key for a child row: the registered key fields, falling
	back to every significant column."""
	key_fields = CHILD_DEDUP_KEYS.get(child_doctype) or sorted(values)
	return tuple(cstr(values.get(f)).strip().lower() for f in key_fields)


def _reconcile_primary_flags(target_doc, fieldname: str, appended_rows: list) -> None:
	"""Ensure at most one row per is_primary* flag survives the union,
	preferring the survivor's existing primary."""
	rows = target_doc.get(fieldname) or []
	if not rows:
		return
	appended = set(id(row) for row in appended_rows)
	meta = frappe.get_meta(rows[0].doctype)
	flag_fields = [
		df.fieldname
		for df in meta.fields
		if df.fieldtype == "Check"
		and (df.fieldname == "is_primary" or df.fieldname.startswith("is_primary_"))
	]
	for flag in flag_fields:
		target_has_primary = any(row.get(flag) for row in rows if id(row) not in appended)
		seen_primary = False
		for row in rows:
			if not row.get(flag):
				continue
			if id(row) in appended and target_has_primary:
				row.set(flag, 0)
			elif seen_primary:
				row.set(flag, 0)
			else:
				seen_primary = True


def _normalize_assign(doctype: str, *names: str) -> None:
	"""Work around a frappe v15 inconsistency: ToDo.update_in_reference
	writes ``_assign = ""`` (empty string, not NULL) on the referenced
	document when no open assigned ToDos remain, and rename_doc's
	update_assignments crashes on it (``frappe.parse_json("")``). Normalize
	to NULL so rename-merge can proceed."""
	for name in names:
		if frappe.db.get_value(doctype, name, "_assign") == "":
			frappe.db.set_value(doctype, name, "_assign", None, update_modified=False)


# ---------------------------------------------------------------------------
# post-merge cleanup and audit
# ---------------------------------------------------------------------------


def _dedupe_repointed_dynamic_links(doctype: str, target: str) -> None:
	"""Collapse duplicate dynamic-link child rows created by repointing.

	A document linked to both merge partners (e.g. an Address linked to both
	suppliers) ends up with two identical rows pointing at the survivor after
	rename-merge. For every child table holding dynamic links to ``doctype``,
	keep the first row of each (parent document, dedup key) group and delete
	the rest.
	"""
	for df in get_dynamic_link_map().get(doctype, []):
		meta = frappe.get_meta(df.parent)
		if not meta.istable or meta.is_virtual:
			continue
		rows = frappe.get_all(
			df.parent,
			filters={df.options: doctype, df.fieldname: target},
			fields=["*"],
			order_by="parent, idx, name",
			ignore_permissions=True,
		)
		seen = set()
		duplicates = []
		affected_parents = set()
		for row in rows:
			values = _significant_child_values(row)
			group = (
				row.parenttype,
				row.parent,
				row.parentfield,
				_child_row_key(values, df.parent),
			)
			if group in seen:
				duplicates.append(row.name)
				affected_parents.add((row.parenttype, row.parent))
			else:
				seen.add(group)
		if duplicates:
			frappe.db.delete(df.parent, {"name": ("in", duplicates)})
			for parenttype, parent in affected_parents:
				frappe.clear_document_cache(parenttype, parent)


def _add_audit_comment(target_doc, source: str, snapshot: str) -> None:
	"""Comment on the survivor recording who merged what and when, with a
	recoverable JSON snapshot of the deleted source document."""
	content = (
		_("Merged from {0} by {1} on {2}").format(
			frappe.bold(source), frappe.session.user, frappe.utils.format_datetime(now())
		)
		+ "<details><summary>"
		+ _("Source document snapshot (JSON)")
		+ f"</summary><pre>{frappe.utils.escape_html(snapshot)}</pre></details>"
	)
	target_doc.add_comment("Comment", content)


# ---------------------------------------------------------------------------
# field helpers
# ---------------------------------------------------------------------------


def _is_resolvable_field(df) -> bool:
	"""True for fields a user can meaningfully resolve: persisted, writable,
	not derived from elsewhere. Password fields are excluded — documents only
	carry ``*****`` dummies (real values live in the encrypted store), so
	there is nothing meaningful to show or resolve."""
	return not (
		df.fieldtype in no_value_fields
		or df.fieldtype in ("Read Only", "Password")
		or df.fieldname in IGNORED_FIELDS
		or df.read_only
		or df.is_virtual
		or df.fetch_from
	)


def _allowed_read_permlevels(doc) -> set:
	"""Permlevels the current user may read on this doctype. Administrator
	sees everything, mirroring frappe's validate_higher_perm_levels."""
	if frappe.session.user == "Administrator":
		return {df.permlevel for df in doc.meta.fields} | {0}
	return set(doc.get_permlevel_access("read"))


def _snapshot_dict(doc) -> dict:
	"""The source document as a dict for the audit snapshot, minus Password
	fields and fields at permlevels the merging user cannot read — the
	comment is visible to anyone who can read the survivor, so it must not
	widen field-level read permissions."""
	allowed = _allowed_read_permlevels(doc)
	data = doc.as_dict(convert_dates_to_str=True)
	for df in doc.meta.fields:
		if df.fieldtype == "Password" or df.permlevel not in allowed:
			data.pop(df.fieldname, None)
		elif df.fieldtype in table_fields:
			child_meta = frappe.get_meta(df.options)
			hidden = [
				cdf.fieldname
				for cdf in child_meta.fields
				if cdf.fieldtype == "Password" or cdf.permlevel not in allowed
			]
			for row in data.get(df.fieldname) or []:
				for fieldname in hidden:
					row.pop(fieldname, None)
	return data


def _is_empty(value) -> bool:
	return value is None or value == ""


def _values_equal(a, b) -> bool:
	if _is_empty(a) and _is_empty(b):
		return True
	return a == b
