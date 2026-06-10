# Copyright (c) 2026, GreenSight Agronomics and contributors
# License: GNU General Public License v3. See license.txt
"""Whitelisted endpoints for the merge UI.

Thin wrappers around :mod:`merge_tool.merge_engine`; all validation and
permission checks live in the engine so it stays safe regardless of the
entry point.
"""

import frappe

from merge_tool import merge_engine


@frappe.whitelist()
def get_mergable_doctypes() -> list[str]:
	"""Doctypes the current site allows merging for (registry + hooks)."""
	return merge_engine.get_mergable_doctypes()


@frappe.whitelist()
def get_merge_impact(doctype: str, source: str, target: str) -> dict:
	"""Counts of documents linking to each record, grouped by doctype."""
	return merge_engine.get_merge_impact(doctype, source, target)


@frappe.whitelist()
def get_merge_preview(doctype: str, source: str, target: str) -> dict:
	"""Field-by-field diff with auto-resolution status and child table preview."""
	return merge_engine.get_merge_preview(doctype, source, target)


@frappe.whitelist()
def merge_docs(doctype: str, source: str, target: str, resolutions: dict | str | None = None) -> dict:
	"""Merge source into target. See :func:`merge_tool.merge_engine.merge_docs`."""
	return merge_engine.merge_docs(doctype, source, target, resolutions)
