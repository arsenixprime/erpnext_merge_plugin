# Copyright (c) 2026, GreenSight Agronomics and contributors
# License: GNU General Public License v3. See license.txt
"""Seed data for docs/MANUAL_TESTING.md. Idempotent — cleans up its own
records first. Run with:

    bench --site dev.localhost execute merge_tool.dev_seed.run
"""

import frappe
from frappe.utils import add_days, now_datetime, today


def run():
	_cleanup()
	alice, alicia = _seed_contact_pair()
	_seed_supplier_pair()
	bob, robert = _seed_linked_contact_pair()
	frappe.db.commit()
	print(
		"SEEDED contacts:", alice.name, "|", alicia.name,
		"| linked contacts:", bob.name, "|", robert.name,
	)


def _cleanup():
	for doctype, filters in (
		("Purchase Order", {"supplier": ("like", "Manual Test Supplier%")}),
		("ToDo", {"reference_type": "Contact", "description": ("like", "%manual merge test%")}),
		("Event", {"subject": ("like", "Manual Test%")}),
		("Communication", {"subject": ("like", "Manual Test%")}),
		("Address", {"address_title": ("like", "Manual Test%")}),
		("Contact", {"company_name": "Manual Test"}),
		("Customer", {"name": ("like", "Manual Test Customer%")}),
		("Supplier", {"name": ("like", "Manual Test Supplier%")}),
	):
		for name in frappe.get_all(doctype, filters=filters, pluck="name"):
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)


def _seed_contact_pair():
	"""Walkthrough section 1: simple field conflicts and child-table union."""
	alice = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": "Alice",
			"designation": "Engineer",
			"company_name": "Manual Test",
			"email_ids": [
				{"email_id": "shared@example.com", "is_primary": 1},
				{"email_id": "alice@example.com", "is_primary": 0},
			],
			"phone_nos": [{"phone": "+1-555-0100", "is_primary_phone": 1}],
		}
	).insert()
	alicia = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": "Alicia",
			"designation": "Manager",
			"company_name": "Manual Test",
			"email_ids": [
				{"email_id": "shared@example.com", "is_primary": 1},
				{"email_id": "alicia@example.com", "is_primary": 0},
			],
		}
	).insert()
	alice.add_comment("Comment", "merge test comment")
	frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "merge-walkthrough.txt",
			"content": "attachment that should follow the merge",
			"attached_to_doctype": "Contact",
			"attached_to_name": alice.name,
		}
	).insert()
	return alice, alicia


def _seed_supplier_pair():
	"""Walkthrough section 5: PO repointing and shared-address link dedup."""
	_ensure_fiscal_year()
	_ensure("Supplier Group", "All Supplier Groups", {"supplier_group_name": "All Supplier Groups", "is_group": 1})
	s1 = frappe.get_doc(
		{"doctype": "Supplier", "supplier_name": "Manual Test Supplier One", "supplier_group": "All Supplier Groups"}
	).insert()
	s2 = frappe.get_doc(
		{"doctype": "Supplier", "supplier_name": "Manual Test Supplier Two", "supplier_group": "All Supplier Groups"}
	).insert()

	from merge_tool.tests.test_merge_engine import get_or_make_company, get_or_make_item

	frappe.get_doc(
		{
			"doctype": "Purchase Order",
			"supplier": s1.name,
			"company": get_or_make_company(),
			"transaction_date": today(),
			"schedule_date": add_days(today(), 7),
			"items": [
				{"item_code": get_or_make_item(), "qty": 1, "rate": 100, "schedule_date": add_days(today(), 7)}
			],
		}
	).insert()

	if not frappe.db.exists("Address Template", {"is_default": 1}):
		frappe.get_doc({"doctype": "Address Template", "country": "United States", "is_default": 1}).insert()
	_make_address(
		"Manual Test Shared HQ",
		links=[("Supplier", s1.name), ("Supplier", s2.name)],
	)


def _seed_linked_contact_pair():
	"""Contacts rich in dynamic links, plus documents linking back at them,
	so the impact table and repointing are visible in the UI:

	- Bob and Robert both link to Customer Acme (dedup demo) and Bob also to
	  Customer Beta (disjoint link survives the union).
	- Their `address` Link fields point at different Addresses → a Link-type
	  conflict row in the dialog.
	- Inbound references: 2 ToDos + 1 Communication + 2 Events at Bob,
	  1 ToDo + 1 Communication + 1 shared Event at Robert, and Customer
	  Acme's primary contact is Bob.
	"""
	_ensure("Customer Group", "All Customer Groups", {"customer_group_name": "All Customer Groups", "is_group": 1})
	_ensure("Territory", "All Territories", {"territory_name": "All Territories", "is_group": 1})
	# customers must use leaf (non-group) nodes
	_ensure(
		"Customer Group",
		"Manual Test Group",
		{"customer_group_name": "Manual Test Group", "parent_customer_group": "All Customer Groups"},
	)
	_ensure(
		"Territory",
		"Manual Test Territory",
		{"territory_name": "Manual Test Territory", "parent_territory": "All Territories"},
	)
	acme = _make_customer("Manual Test Customer Acme")
	beta = _make_customer("Manual Test Customer Beta")

	bob_office = _make_address("Manual Test Bob Office")
	robert_office = _make_address("Manual Test Robert Office")

	bob = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": "Bob",
			"designation": "Buyer",
			"company_name": "Manual Test",
			"address": bob_office.name,
			"email_ids": [{"email_id": "bob@example.com", "is_primary": 1}],
			"phone_nos": [{"phone": "+1-555-0200", "is_primary_phone": 1}],
			"links": [
				{"link_doctype": "Customer", "link_name": acme.name},
				{"link_doctype": "Customer", "link_name": beta.name},
			],
		}
	).insert()
	robert = frappe.get_doc(
		{
			"doctype": "Contact",
			"first_name": "Robert",
			"designation": "Purchasing Lead",
			"company_name": "Manual Test",
			"address": robert_office.name,
			"email_ids": [
				{"email_id": "bob@example.com", "is_primary": 0},
				{"email_id": "robert@example.com", "is_primary": 1},
			],
			"phone_nos": [{"phone": "+1-555-0201", "is_primary_phone": 1}],
			"links": [{"link_doctype": "Customer", "link_name": acme.name}],
		}
	).insert()

	# Customer Acme points back at Bob through a plain Link field
	acme.db_set("customer_primary_contact", bob.name)

	for contact, descriptions in (
		(bob, ["call about the manual merge test order", "send manual merge test samples"]),
		(robert, ["review manual merge test contract"]),
	):
		for description in descriptions:
			frappe.get_doc(
				{
					"doctype": "ToDo",
					"description": description,
					"reference_type": "Contact",
					"reference_name": contact.name,
				}
			).insert()

	for contact in (bob, robert):
		frappe.get_doc(
			{
				"doctype": "Communication",
				"communication_type": "Communication",
				"communication_medium": "Email",
				"sent_or_received": "Received",
				"subject": f"Manual Test email to {contact.first_name}",
				"content": "lorem ipsum",
				"reference_doctype": "Contact",
				"reference_name": contact.name,
			}
		).insert()

	# one event with both contacts (participants collapse to one row after
	# merge), one with Bob alone
	frappe.get_doc(
		{
			"doctype": "Event",
			"subject": "Manual Test kickoff with both",
			"starts_on": now_datetime(),
			"event_participants": [
				{"reference_doctype": "Contact", "reference_docname": bob.name},
				{"reference_doctype": "Contact", "reference_docname": robert.name},
			],
		}
	).insert()
	frappe.get_doc(
		{
			"doctype": "Event",
			"subject": "Manual Test follow-up with Bob",
			"starts_on": now_datetime(),
			"event_participants": [{"reference_doctype": "Contact", "reference_docname": bob.name}],
		}
	).insert()

	return bob, robert


def _ensure(doctype, name, values):
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert()


def _ensure_fiscal_year():
	"""Sites where the setup wizard never ran have no Fiscal Year, which
	makes ERPNext party dashboards (e.g. the Supplier form) throw
	FiscalYearError for any date."""
	year = today()[:4]
	exists = frappe.db.exists(
		"Fiscal Year",
		{"year_start_date": ("<=", today()), "year_end_date": (">=", today()), "disabled": 0},
	)
	if not exists:
		frappe.get_doc(
			{
				"doctype": "Fiscal Year",
				"year": year,
				"year_start_date": f"{year}-01-01",
				"year_end_date": f"{year}-12-31",
			}
		).insert(ignore_permissions=True)


def _make_customer(customer_name):
	return frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": customer_name,
			"customer_group": "Manual Test Group",
			"territory": "Manual Test Territory",
		}
	).insert()


def _make_address(address_title, links=None):
	return frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": address_title,
			"address_type": "Billing",
			"address_line1": "1 Main Street",
			"city": "Boston",
			"country": "United States",
			"links": [{"link_doctype": dt, "link_name": name} for dt, name in (links or [])],
		}
	).insert()
