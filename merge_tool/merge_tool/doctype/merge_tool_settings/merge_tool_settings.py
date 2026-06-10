# Copyright (c) 2026, GreenSight Agronomics and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class MergeToolSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		from merge_tool.merge_tool.doctype.merge_tool_enabled_doctype.merge_tool_enabled_doctype import (
			MergeToolEnabledDocType,
		)

		default_concat_separator: DF.Data | None
		enabled_doctypes: DF.Table[MergeToolEnabledDocType]
	# end: auto-generated types

	def validate(self):
		seen = set()
		for row in self.enabled_doctypes:
			if row.document_type in seen:
				frappe.throw(_("{0} is listed more than once").format(row.document_type))
			seen.add(row.document_type)

			meta = frappe.get_meta(row.document_type)
			if meta.issingle or meta.istable or meta.is_virtual:
				frappe.throw(
					_("{0} cannot be enabled for merging (single, child and virtual doctypes are not supported)").format(
						row.document_type
					)
				)
