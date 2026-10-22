// Copyright (c) 2026, GreenSight Agronomics and contributors
// License: GNU General Public License v3. See license.txt

// Adds a "Merge" entry to the list view (and report view) Actions menu for
// doctypes enabled in Merge Tool Settings. Enabled only when exactly two
// rows are selected.
//
// PATCH NOTE: frappe v15/v16 have no extension point for adding bulk actions
// across doctypes, so this wraps ListView.prototype.set_actions_menu_items:
// the original runs unchanged, then the Merge item is appended through the
// public page.add_actions_menu_item API. ReportView inherits the same
// method, so both views are covered. Checked against
// frappe/public/js/frappe/list/list_view.js (v15 and v16); re-verify on framework
// upgrades. See CLAUDE.md.

frappe.provide("merge_tool");

merge_tool.get_mergable_doctypes = function () {
	if (!merge_tool._mergable_doctypes) {
		merge_tool._mergable_doctypes = frappe
			.call("merge_tool.api.get_mergable_doctypes")
			.then((r) => r.message || []);
	}
	return merge_tool._mergable_doctypes;
};

$(function () {
	if (!frappe.views || !frappe.views.ListView) return;

	const original = frappe.views.ListView.prototype.set_actions_menu_items;
	frappe.views.ListView.prototype.set_actions_menu_items = function () {
		original.call(this);

		merge_tool.get_mergable_doctypes().then((doctypes) => {
			if (!doctypes.includes(this.doctype)) return;
			if (!frappe.model.can_write(this.doctype) || !frappe.model.can_delete(this.doctype)) {
				return;
			}

			const $item = this.page.add_actions_menu_item(
				__("Merge"),
				() => {
					const names = this.get_checked_items(true);
					if (names.length !== 2) {
						frappe.msgprint(__("Select exactly two records to merge."));
						return;
					}
					merge_tool.show_merge_dialog(this.doctype, names);
				},
				true
			);
			if (!$item) return;
			$item.addClass("merge-tool-action");

			// enable the item only while exactly two rows are checked. The
			// state is refreshed in the capture phase on the button group, so
			// it runs before whichever dropdown opens the menu (v15: bootstrap;
			// v16: frappe.ui.Dropdown, which copies the item's "disabled" class
			// when it opens and fires no bootstrap events).
			const refresh = () => {
				const can_merge = this.get_checked_items(true).length === 2;
				$item.toggleClass("disabled", !can_merge);
				$item.attr(
					"title",
					can_merge ? "" : __("Select exactly two records to merge")
				);
			};
			const group = this.page.actions_btn_group.get(0);
			group.addEventListener("click", refresh, true);
			group.addEventListener("keydown", refresh, true);
		});
	};
});
