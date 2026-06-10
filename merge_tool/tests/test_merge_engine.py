# Copyright (c) 2026, GreenSight Agronomics and contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from merge_tool.merge_engine import get_merge_impact, get_merge_preview, merge_docs


def _suffix():
	return frappe.generate_hash(length=8)


def make_contact(first_name, emails=(), phones=(), links=(), **kwargs):
	"""emails/phones: list of (value, is_primary). links: list of (doctype, name)."""
	contact = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": first_name,
			**kwargs,
		}
	)
	for email, is_primary in emails:
		contact.append("email_ids", {"email_id": email, "is_primary": is_primary})
	for phone, is_primary in phones:
		contact.append("phone_nos", {"phone": phone, "is_primary_phone": is_primary})
	for link_doctype, link_name in links:
		contact.append("links", {"link_doctype": link_doctype, "link_name": link_name})
	return contact.insert()


class TestMergeEngineContacts(FrappeTestCase):
	def test_contact_merge_unions_children_and_repoints(self):
		s = _suffix()
		user_a = make_user(f"merge-link-a-{s}@example.com")
		user_b = make_user(f"merge-link-b-{s}@example.com")

		source = make_contact(
			f"Source{s}",
			emails=[(f"shared-{s}@example.com", 0), (f"source-only-{s}@example.com", 1)],
			phones=[("+1-555-0100", 1)],
			links=[("User", user_a.name), ("User", user_b.name)],
		)
		target = make_contact(
			f"Target{s}",
			emails=[(f"shared-{s}@example.com", 0), (f"target-only-{s}@example.com", 1)],
			phones=[("+1-555-0100", 0), ("+1-555-0199", 1)],
			links=[("User", user_a.name)],
		)

		source.add_comment("Comment", f"source comment {s}")
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"merge-test-{s}.txt",
				"content": "merge test attachment",
				"attached_to_doctype": "Contact",
				"attached_to_name": source.name,
			}
		).insert()

		merge_docs("Contact", source.name, target.name, {"first_name": {"use": "target"}})

		self.assertFalse(frappe.db.exists("Contact", source.name))
		survivor = frappe.get_doc("Contact", target.name)

		emails = {row.email_id for row in survivor.email_ids}
		self.assertEqual(
			emails,
			{f"shared-{s}@example.com", f"source-only-{s}@example.com", f"target-only-{s}@example.com"},
		)
		# shared email deduped: 3 rows, not 4
		self.assertEqual(len(survivor.email_ids), 3)
		# exactly one primary email, and it is the survivor's original one
		primaries = [row.email_id for row in survivor.email_ids if row.is_primary]
		self.assertEqual(primaries, [f"target-only-{s}@example.com"])

		phones = [row.phone for row in survivor.phone_nos]
		self.assertEqual(sorted(phones), ["+1-555-0100", "+1-555-0199"])
		primary_phones = [row.phone for row in survivor.phone_nos if row.is_primary_phone]
		self.assertEqual(primary_phones, ["+1-555-0199"])

		link_pairs = [(row.link_doctype, row.link_name) for row in survivor.links]
		self.assertEqual(len(link_pairs), 2)
		self.assertEqual(set(link_pairs), {("User", user_a.name), ("User", user_b.name)})

		# comment and file repointed by rename-merge
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Contact",
					"reference_name": target.name,
					"content": ("like", f"%source comment {s}%"),
				},
			)
		)
		file.reload()
		self.assertEqual(file.attached_to_name, target.name)

		# audit comment with snapshot present
		audit = frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "Contact",
				"reference_name": target.name,
				"content": ("like", "%Merged from%"),
			},
			pluck="content",
		)
		self.assertTrue(audit)
		self.assertIn(source.name, audit[0])

	def test_field_resolutions_pick_and_concat(self):
		s = _suffix()
		source = make_contact(f"SrcName{s}", middle_name=f"SrcMid{s}", designation="Engineer")
		target = make_contact(f"TgtName{s}", middle_name=f"TgtMid{s}", designation="Manager")

		preview = get_merge_preview("Contact", source.name, target.name)
		conflicts = {f["fieldname"]: f for f in preview["fields"] if f["status"] == "conflict"}
		self.assertIn("first_name", conflicts)
		self.assertIn("designation", conflicts)
		self.assertTrue(conflicts["designation"]["can_concat"])

		merge_docs(
			"Contact",
			source.name,
			target.name,
			{
				"first_name": {"use": "source"},
				"middle_name": {"use": "target"},
				"designation": {"concat": True, "separator": " / "},
			},
		)

		survivor = frappe.get_doc("Contact", target.name)
		self.assertEqual(survivor.first_name, f"SrcName{s}")
		self.assertEqual(survivor.middle_name, f"TgtMid{s}")
		self.assertEqual(survivor.designation, "Manager / Engineer")

	def test_auto_resolution_fills_empty_target_fields(self):
		s = _suffix()
		source = make_contact(f"Auto{s}", designation="Director")
		target = make_contact(f"Auto{s}")  # same first name, no designation

		preview = get_merge_preview("Contact", source.name, target.name)
		by_name = {f["fieldname"]: f for f in preview["fields"]}
		self.assertEqual(by_name["first_name"]["status"], "auto")
		self.assertEqual(by_name["designation"]["status"], "auto")
		self.assertEqual(by_name["designation"]["auto_resolution"], "use_source")

		merge_docs("Contact", source.name, target.name, {})
		self.assertEqual(frappe.db.get_value("Contact", target.name, "designation"), "Director")

	def test_merge_survives_empty_string_assign(self):
		# frappe's ToDo.update_in_reference writes _assign="" (not NULL) on
		# the referenced doc, which crashes stock rename-merge; the engine
		# normalizes it (see _normalize_assign)
		s = _suffix()
		source = make_contact(f"AssignSrc{s}")
		target = make_contact(f"AssignTgt{s}")
		for contact in (source, target):
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"description": f"unassigned todo {s}",
					"reference_type": "Contact",
					"reference_name": contact.name,
				}
			).insert()
		self.assertEqual(frappe.db.get_value("Contact", source.name, "_assign"), "")

		merge_docs("Contact", source.name, target.name, {"first_name": {"use": "target"}})

		self.assertFalse(frappe.db.exists("Contact", source.name))
		todos = frappe.get_all(
			"ToDo",
			filters={"reference_type": "Contact", "reference_name": target.name},
			pluck="name",
		)
		self.assertEqual(len(todos), 2)

	def test_missing_resolution_refused(self):
		s = _suffix()
		source = make_contact(f"NoRes{s}", designation="A")
		target = make_contact(f"NoResOther{s}", designation="B")

		self.assertRaisesRegex(
			frappe.ValidationError,
			"No resolution provided",
			merge_docs,
			"Contact",
			source.name,
			target.name,
			{},
		)

	def test_unregistered_doctype_refused(self):
		todo_a = frappe.get_doc({"doctype": "ToDo", "description": "a"}).insert()
		todo_b = frappe.get_doc({"doctype": "ToDo", "description": "b"}).insert()
		self.assertRaisesRegex(
			frappe.ValidationError,
			"not enabled",
			merge_docs,
			"ToDo",
			todo_a.name,
			todo_b.name,
			{},
		)

	def test_same_doc_refused(self):
		s = _suffix()
		contact = make_contact(f"Same{s}")
		self.assertRaisesRegex(
			frappe.ValidationError,
			"must be different",
			merge_docs,
			"Contact",
			contact.name,
			contact.name,
			{},
		)

	def test_permission_denied_for_non_privileged_user(self):
		s = _suffix()
		source = make_contact(f"PermSrc{s}")
		target = make_contact(f"PermTgt{s}")
		user = make_user(f"merge-noperm-{s}@example.com")

		frappe.set_user(user.name)
		try:
			self.assertRaises(
				frappe.PermissionError,
				merge_docs,
				"Contact",
				source.name,
				target.name,
				{},
			)
		finally:
			frappe.set_user("Administrator")


class TestMergeEngineUniqueness(FrappeTestCase):
	def test_unique_collision_fails_cleanly(self):
		# User.username and User.email are unique fields; a concat resolution
		# can manufacture a value that collides with a third user.
		s = _suffix()
		enable_doctype("User")
		bystander = make_user(f"merge-bystander-{s}@example.com", username=f"alpha{s}beta{s}")
		source = make_user(f"merge-uq-src-{s}@example.com", username=f"beta{s}")
		target = make_user(f"merge-uq-tgt-{s}@example.com", username=f"alpha{s}")
		target_before = frappe.get_doc("User", target.name).as_dict()

		resolutions = {
			"email": {"use": "target"},
			"first_name": {"use": "target"},
			"username": {"concat": True, "separator": ""},
		}

		self.assertRaisesRegex(
			frappe.ValidationError,
			"must be unique",
			merge_docs,
			"User",
			source.name,
			target.name,
			resolutions,
		)

		# clean failure: both docs still exist and are unmodified
		self.assertTrue(frappe.db.exists("User", source.name))
		self.assertEqual(frappe.db.get_value("User", source.name, "username"), f"beta{s}")
		target_after = frappe.get_doc("User", target.name).as_dict()
		self.assertEqual(target_before.username, target_after.username)
		self.assertEqual(target_before.modified, target_after.modified)
		self.assertEqual(bystander.username, f"alpha{s}beta{s}")

	def test_survivor_can_adopt_source_unique_value(self):
		s = _suffix()
		enable_doctype("User")
		source = make_user(f"merge-adopt-src-{s}@example.com", username=f"adoptme{s}")
		target = make_user(f"merge-adopt-tgt-{s}@example.com", username=f"olduser{s}")

		merge_docs(
			"User",
			source.name,
			target.name,
			{
				"email": {"use": "target"},
				"first_name": {"use": "target"},
				"username": {"use": "source"},
			},
		)

		self.assertFalse(frappe.db.exists("User", source.name))
		self.assertEqual(frappe.db.get_value("User", target.name, "username"), f"adoptme{s}")


class TestMergeEngineSuppliers(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_or_make_company()
		cls.item = get_or_make_item()

	def test_supplier_merge_repoints_po_and_dedupes_shared_address(self):
		s = _suffix()
		source = make_supplier(f"Merge Src Supplier {s}")
		target = make_supplier(f"Merge Tgt Supplier {s}")

		po = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": source.name,
				"company": self.company,
				"transaction_date": today(),
				"schedule_date": add_days(today(), 7),
				"items": [
					{
						"item_code": self.item,
						"qty": 1,
						"rate": 100,
						"schedule_date": add_days(today(), 7),
					}
				],
			}
		).insert()

		if not frappe.db.exists("Address Template", {"is_default": 1}):
			frappe.get_doc(
				{"doctype": "Address Template", "country": "United States", "is_default": 1}
			).insert()

		address = frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": f"Shared Address {s}",
				"address_type": "Billing",
				"address_line1": "1 Main Street",
				"city": "Boston",
				"country": "United States",
				"links": [
					{"link_doctype": "Supplier", "link_name": source.name},
					{"link_doctype": "Supplier", "link_name": target.name},
				],
			}
		).insert()

		impact = get_merge_impact("Supplier", source.name, target.name)
		self.assertEqual(impact["source"].get("Purchase Order"), 1)
		self.assertEqual(impact["source"].get("Address"), 1)
		self.assertEqual(impact["target"].get("Address"), 1)

		merge_docs(
			"Supplier",
			source.name,
			target.name,
			{"supplier_name": {"use": "target"}},
		)

		self.assertFalse(frappe.db.exists("Supplier", source.name))
		self.assertEqual(frappe.db.get_value("Purchase Order", po.name, "supplier"), target.name)

		# the shared address must end up with exactly one link row to the survivor
		address_links = frappe.get_all(
			"Dynamic Link",
			filters={
				"parenttype": "Address",
				"parent": address.name,
				"link_doctype": "Supplier",
			},
			fields=["link_name"],
		)
		self.assertEqual([row.link_name for row in address_links], [target.name])


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def make_user(email, username=None):
	return frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": email.split("@")[0],
			"username": username,
			"send_welcome_email": 0,
		}
	).insert(ignore_permissions=True)


def enable_doctype(doctype):
	settings = frappe.get_doc("Merge Tool Settings")
	if not any(row.document_type == doctype for row in settings.enabled_doctypes):
		settings.append("enabled_doctypes", {"document_type": doctype})
		settings.save(ignore_permissions=True)


def make_supplier(supplier_name):
	if not frappe.db.exists("Supplier Group", "All Supplier Groups"):
		frappe.get_doc(
			{"doctype": "Supplier Group", "supplier_group_name": "All Supplier Groups", "is_group": 1}
		).insert()
	return frappe.get_doc(
		{
			"doctype": "Supplier",
			"supplier_name": supplier_name,
			"supplier_group": "All Supplier Groups",
		}
	).insert()


def get_or_make_company():
	existing = frappe.get_all("Company", limit=1, pluck="name")
	if existing:
		return existing[0]
	# bare sites (setup wizard not run) lack the warehouse types that
	# Company.on_update needs to create its default warehouses
	for warehouse_type in ("Transit",):
		if not frappe.db.exists("Warehouse Type", warehouse_type):
			frappe.get_doc({"doctype": "Warehouse Type", "name": warehouse_type}).insert()
	return (
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "Merge Test Co",
				"abbr": "MTC",
				"default_currency": "USD",
				"country": "United States",
			}
		)
		.insert()
		.name
	)


def get_or_make_item():
	if frappe.db.exists("Item", "MERGE-TEST-ITEM"):
		return "MERGE-TEST-ITEM"
	if not frappe.db.exists("UOM", "Nos"):
		frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert()
	if not frappe.db.exists("Item Group", "All Item Groups"):
		frappe.get_doc(
			{"doctype": "Item Group", "item_group_name": "All Item Groups", "is_group": 1}
		).insert()
	return (
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": "MERGE-TEST-ITEM",
				"item_name": "Merge Test Item",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 0,
			}
		)
		.insert()
		.name
	)
