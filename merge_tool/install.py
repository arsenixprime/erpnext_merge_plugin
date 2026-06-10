# Copyright (c) 2026, GreenSight Agronomics and contributors
# License: GNU General Public License v3. See license.txt

import frappe

DEFAULT_MERGABLE_DOCTYPES = ("Contact", "Supplier", "Customer", "Address", "Lead")


def after_install():
	seed_default_mergable_doctypes()


def seed_default_mergable_doctypes():
	"""Enable the default doctypes in Merge Tool Settings, skipping any that
	are not installed on this site (e.g. ERPNext masters on a bare frappe
	site) and any already enabled."""
	settings = frappe.get_doc("Merge Tool Settings")
	enabled = {row.document_type for row in settings.enabled_doctypes}
	changed = False
	for doctype in DEFAULT_MERGABLE_DOCTYPES:
		if doctype in enabled or not frappe.db.exists("DocType", doctype):
			continue
		settings.append("enabled_doctypes", {"document_type": doctype})
		changed = True
	if changed:
		settings.save(ignore_permissions=True)
