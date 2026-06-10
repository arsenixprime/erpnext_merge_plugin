// Copyright (c) 2026, GreenSight Agronomics and contributors
// License: GNU General Public License v3. See license.txt

frappe.provide("merge_tool");

/**
 * Interactive merge dialog for two records of the same doctype.
 *
 * Usage: merge_tool.show_merge_dialog("Contact", ["CONT-0001", "CONT-0002"])
 *
 * The user picks the survivor, resolves field conflicts (pick a side or
 * concatenate text), chooses child-table strategies, confirms, and the
 * server-side engine (merge_tool.api.merge_docs) does the rest.
 */
merge_tool.show_merge_dialog = function (doctype, names) {
	if (!Array.isArray(names) || names.length !== 2) {
		frappe.msgprint(__("Select exactly two {0} records to merge.", [__(doctype)]));
		return;
	}
	new merge_tool.MergeDialog(doctype, names).show();
};

merge_tool.MergeDialog = class MergeDialog {
	constructor(doctype, names) {
		this.doctype = doctype;
		this.names = names;
		// until the user flips: names[0] merges into names[1]
		this.source = names[0];
		this.target = names[1];
		this.preview = null;
		this.impact = null;
	}

	async show() {
		await this.load_record_info();
		this.pick_default_survivor();

		this.dialog = new frappe.ui.Dialog({
			title: __("Merge {0}", [__(this.doctype)]),
			size: "extra-large",
			fields: [
				{ fieldname: "survivor_html", fieldtype: "HTML" },
				{ fieldname: "impact_html", fieldtype: "HTML" },
				{ fieldname: "conflicts_html", fieldtype: "HTML" },
				{ fieldname: "children_html", fieldtype: "HTML" },
			],
			primary_action_label: __("Merge"),
			primary_action: () => this.confirm_and_merge(),
		});

		await this.refresh_preview();
		this.render_survivor_chooser();
		this.dialog.show();
	}

	async load_record_info() {
		const [impact, records] = await Promise.all([
			frappe
				.call("merge_tool.api.get_merge_impact", {
					doctype: this.doctype,
					source: this.names[0],
					target: this.names[1],
				})
				.then((r) => r.message),
			frappe.db.get_list(this.doctype, {
				filters: { name: ["in", this.names] },
				fields: ["name", "creation"],
				limit: 2,
			}),
		]);
		// impact keyed by record name regardless of direction
		this.impact_by_name = {
			[this.names[0]]: impact.source,
			[this.names[1]]: impact.target,
		};
		this.creation_by_name = {};
		for (const record of records) {
			this.creation_by_name[record.name] = record.creation;
		}
	}

	total_links(name) {
		return Object.values(this.impact_by_name[name] || {}).reduce((a, b) => a + b, 0);
	}

	pick_default_survivor() {
		// default survivor: the most-linked record; tie broken by age (older wins)
		const [a, b] = this.names;
		const links_a = this.total_links(a);
		const links_b = this.total_links(b);
		let survivor;
		if (links_a !== links_b) {
			survivor = links_a > links_b ? a : b;
		} else {
			survivor = this.creation_by_name[a] <= this.creation_by_name[b] ? a : b;
		}
		this.target = survivor;
		this.source = survivor === a ? b : a;
	}

	async refresh_preview() {
		this.preview = await frappe
			.call("merge_tool.api.get_merge_preview", {
				doctype: this.doctype,
				source: this.source,
				target: this.target,
			})
			.then((r) => r.message);
		this.resolutions = {};
		this.render_impact();
		this.render_conflicts();
		this.render_children();
	}

	async flip_survivor(survivor) {
		if (survivor === this.target) return;
		[this.source, this.target] = [this.target, this.source];
		this.dialog.get_primary_btn().prop("disabled", true);
		await this.refresh_preview();
		this.dialog.get_primary_btn().prop("disabled", false);
		this.render_survivor_chooser();
	}

	// ------------------------------------------------------------------
	// rendering
	// ------------------------------------------------------------------

	render_survivor_chooser() {
		const wrapper = this.dialog.fields_dict.survivor_html.$wrapper;
		const card = (name) => {
			const is_survivor = name === this.target;
			return `
				<label class="merge-tool-survivor-card ${is_survivor ? "selected" : ""}"
						style="flex:1; border:1px solid var(--border-color); border-radius:8px;
						padding:12px; cursor:pointer;
						${is_survivor ? "outline:2px solid var(--primary); background-color: var(--control-bg);" : ""}">
					<input type="radio" name="merge-tool-survivor" value="${frappe.utils.escape_html(name)}"
						${is_survivor ? "checked" : ""} style="margin-right:8px;">
					<b>${frappe.utils.escape_html(name)}</b>
					${is_survivor ? `<span class="indicator-pill green" style="margin-left:8px;">${__("Survivor")}</span>` : `<span class="indicator-pill red" style="margin-left:8px;">${__("Will be deleted")}</span>`}
					<div class="text-muted small" style="margin-top:6px;">
						${__("Created {0}", [frappe.datetime.str_to_user(this.creation_by_name[name])])}
						&middot; ${__("{0} linked documents", [this.total_links(name)])}
					</div>
				</label>`;
		};
		wrapper.html(`
			<div class="merge-tool-survivor" style="display:flex; gap:12px; margin-bottom:8px;">
				${card(this.names[0])}${card(this.names[1])}
			</div>
			<p class="text-muted small">${__("The survivor keeps its ID. The other record's data and references are folded into it, then it is deleted.")}</p>
		`);
		wrapper.find("input[name='merge-tool-survivor']").on("change", (e) => {
			this.flip_survivor(e.target.value);
		});
	}

	render_impact() {
		const wrapper = this.dialog.fields_dict.impact_html.$wrapper;
		const rows = [];
		const doctypes = new Set([
			...Object.keys(this.impact_by_name[this.source] || {}),
			...Object.keys(this.impact_by_name[this.target] || {}),
		]);
		for (const dt of [...doctypes].sort()) {
			rows.push(`
				<tr>
					<td>${frappe.utils.escape_html(__(dt))}</td>
					<td class="text-right">${this.impact_by_name[this.source][dt] || 0}</td>
					<td class="text-right">${this.impact_by_name[this.target][dt] || 0}</td>
				</tr>`);
		}
		if (!rows.length) {
			wrapper.html(`<p class="text-muted">${__("No other documents link to either record.")}</p>`);
			return;
		}
		wrapper.html(`
			<details open>
				<summary style="cursor:pointer;"><b>${__("Linked documents to be repointed")}</b></summary>
				<table class="table table-bordered table-sm" style="margin-top:8px;">
					<thead><tr>
						<th>${__("DocType")}</th>
						<th class="text-right">${frappe.utils.escape_html(this.source)} (${__("merges in")})</th>
						<th class="text-right">${frappe.utils.escape_html(this.target)} (${__("survivor")})</th>
					</tr></thead>
					<tbody>${rows.join("")}</tbody>
				</table>
			</details>
		`);
	}

	format_value(field, value) {
		if (value === null || value === undefined || value === "") {
			return `<span class="text-muted">${__("(empty)")}</span>`;
		}
		// frappe.format does NOT HTML-escape every fieldtype (Data passes
		// markup through; Text Editor renders raw HTML), so only use it for
		// fieldtypes whose formatters emit numbers/dates, and escape
		// everything else ourselves — record values are untrusted.
		const NUMERIC_OR_DATE = [
			"Currency",
			"Float",
			"Int",
			"Percent",
			"Check",
			"Date",
			"Datetime",
			"Time",
			"Duration",
			"Rating",
		];
		if (NUMERIC_OR_DATE.includes(field.fieldtype)) {
			return frappe.format(value, { fieldtype: field.fieldtype, options: field.options });
		}
		let text = String(value);
		if (["Text Editor", "HTML Editor", "Markdown Editor", "Code"].includes(field.fieldtype)) {
			// global from frappe/utils/common.js — regex-based, no DOM parsing
			text = strip_html(text);
		}
		if (text.length > 500) {
			text = text.slice(0, 500) + "…";
		}
		return frappe.utils.escape_html(text);
	}

	render_conflicts() {
		const wrapper = this.dialog.fields_dict.conflicts_html.$wrapper;
		const conflicts = this.preview.fields.filter((f) => f.status === "conflict");
		const autos = this.preview.fields.filter((f) => f.status === "auto");

		const conflict_rows = conflicts
			.map((field) => {
				const f = frappe.utils.escape_html(field.fieldname);
				return `
				<tr data-fieldname="${f}">
					<td><b>${frappe.utils.escape_html(field.label)}</b><br>
						<span class="text-muted small">${frappe.utils.escape_html(field.fieldtype)}</span></td>
					<td>
						<label style="font-weight:normal; cursor:pointer;">
							<input type="radio" name="merge-tool-res-${f}" value="source">
							${this.format_value(field, field.source_value)}
						</label>
					</td>
					<td>
						<label style="font-weight:normal; cursor:pointer;">
							<input type="radio" name="merge-tool-res-${f}" value="target" checked>
							${this.format_value(field, field.target_value)}
						</label>
					</td>
					<td>
						${
							field.can_concat
								? `<label style="font-weight:normal; cursor:pointer;">
									<input type="checkbox" class="merge-tool-concat" data-fieldname="${f}">
									${__("Concatenate")}</label>
									<input type="text" class="form-control input-sm merge-tool-separator"
										data-fieldname="${f}" placeholder="${__("separator (default: new line)")}"
										style="display:none; margin-top:4px;">`
								: ""
						}
					</td>
				</tr>`;
			})
			.join("");

		const auto_rows = autos
			.map(
				(field) => `
				<tr>
					<td>${frappe.utils.escape_html(field.label)}</td>
					<td>${this.format_value(field, field.source_value)}</td>
					<td>${this.format_value(field, field.target_value)}</td>
					<td class="text-muted">${
						field.auto_resolution === "use_source" ? __("take from {0}", [frappe.utils.escape_html(this.source)]) : __("no change")
					}</td>
				</tr>`
			)
			.join("");

		wrapper.html(`
			${
				conflicts.length
					? `<h6>${__("Conflicting fields — choose what the survivor keeps")}</h6>
					<table class="table table-bordered table-sm">
						<thead><tr>
							<th style="width:22%;">${__("Field")}</th>
							<th style="width:30%;">${frappe.utils.escape_html(this.source)}</th>
							<th style="width:30%;">${frappe.utils.escape_html(this.target)} (${__("survivor")})</th>
							<th>${__("Options")}</th>
						</tr></thead>
						<tbody>${conflict_rows}</tbody>
					</table>`
					: `<p class="text-success">${__("No conflicting fields.")}</p>`
			}
			${
				autos.length
					? `<details>
						<summary style="cursor:pointer;" class="text-muted">
							${__("No conflict ({0} fields)", [autos.length])}
						</summary>
						<table class="table table-sm" style="margin-top:8px;">
							<thead><tr><th>${__("Field")}</th><th>${frappe.utils.escape_html(this.source)}</th>
							<th>${frappe.utils.escape_html(this.target)}</th><th>${__("Resolution")}</th></tr></thead>
							<tbody>${auto_rows}</tbody>
						</table>
					</details>`
					: ""
			}
		`);

		wrapper.find(".merge-tool-concat").on("change", (e) => {
			const $checkbox = $(e.target);
			const fieldname = $checkbox.data("fieldname");
			const checked = $checkbox.is(":checked");
			wrapper.find(`.merge-tool-separator[data-fieldname='${fieldname}']`).toggle(checked);
			wrapper
				.find(`input[name='merge-tool-res-${fieldname}']`)
				.prop("disabled", checked);
		});
	}

	render_children() {
		const wrapper = this.dialog.fields_dict.children_html.$wrapper;
		if (!this.preview.child_tables.length) {
			wrapper.empty();
			return;
		}

		const sections = this.preview.child_tables
			.map((table) => {
				const f = frappe.utils.escape_html(table.fieldname);
				return `
				<div class="merge-tool-child" data-fieldname="${f}" style="margin-bottom:12px;">
					<div style="display:flex; align-items:center; gap:12px;">
						<b>${frappe.utils.escape_html(table.label)}</b>
						<span class="text-muted small">
							${__("{0} rows from each side", [`${table.source_rows.length} + ${table.target_rows.length}`])}
						</span>
						<select class="form-control input-sm merge-tool-strategy" data-fieldname="${f}"
								style="width:auto; margin-left:auto;">
							<option value="union" selected>${__("Union (keep both, dedupe)")}</option>
							<option value="target_only">${__("Keep only {0}", [frappe.utils.escape_html(this.target)])}</option>
							<option value="source_only">${__("Keep only {0}", [frappe.utils.escape_html(this.source)])}</option>
						</select>
					</div>
					<details style="margin-top:6px;">
						<summary style="cursor:pointer;" class="text-muted small">${__("Preview resulting rows")}</summary>
						<div class="merge-tool-child-preview" style="margin-top:6px;"></div>
					</details>
				</div>`;
			})
			.join("");

		wrapper.html(`<h6>${__("Child tables")}</h6>${sections}`);

		const render_preview = (fieldname) => {
			const table = this.preview.child_tables.find((t) => t.fieldname === fieldname);
			const strategy =
				(this.resolutions[fieldname] && this.resolutions[fieldname].strategy) || "union";
			const rows =
				strategy === "union"
					? table.union_preview
					: strategy === "source_only"
						? table.source_rows
						: table.target_rows;
			const $preview = wrapper.find(
				`.merge-tool-child[data-fieldname='${fieldname}'] .merge-tool-child-preview`
			);
			if (!rows.length) {
				$preview.html(`<p class="text-muted small">${__("No rows")}</p>`);
				return;
			}
			// show columns that hold a value in at least one row, capped for width
			const columns = [];
			for (const row of rows) {
				for (const key of Object.keys(row)) {
					if (row[key] !== null && row[key] !== "" && !columns.includes(key)) {
						columns.push(key);
					}
				}
			}
			const visible = columns.slice(0, 6);
			$preview.html(`
				<table class="table table-bordered table-sm">
					<thead><tr>${visible.map((c) => `<th>${frappe.utils.escape_html(frappe.unscrub(c))}</th>`).join("")}</tr></thead>
					<tbody>
						${rows
							.map(
								(row) =>
									`<tr>${visible
										.map((c) => `<td>${frappe.utils.escape_html(String(row[c] ?? ""))}</td>`)
										.join("")}</tr>`
							)
							.join("")}
					</tbody>
				</table>
			`);
		};

		for (const table of this.preview.child_tables) {
			render_preview(table.fieldname);
		}

		wrapper.find(".merge-tool-strategy").on("change", (e) => {
			const fieldname = $(e.target).data("fieldname");
			this.resolutions[fieldname] = { strategy: e.target.value };
			render_preview(fieldname);
		});
	}

	// ------------------------------------------------------------------
	// submit
	// ------------------------------------------------------------------

	collect_resolutions() {
		const resolutions = { ...this.resolutions };
		const wrapper = this.dialog.fields_dict.conflicts_html.$wrapper;
		for (const field of this.preview.fields) {
			if (field.status !== "conflict") continue;
			const $concat = wrapper.find(`.merge-tool-concat[data-fieldname='${field.fieldname}']`);
			if ($concat.length && $concat.is(":checked")) {
				const separator = wrapper
					.find(`.merge-tool-separator[data-fieldname='${field.fieldname}']`)
					.val();
				resolutions[field.fieldname] = { concat: true };
				if (separator) {
					resolutions[field.fieldname].separator = separator;
				}
			} else {
				const use = wrapper
					.find(`input[name='merge-tool-res-${field.fieldname}']:checked`)
					.val();
				resolutions[field.fieldname] = { use: use || "target" };
			}
		}
		return resolutions;
	}

	confirm_and_merge() {
		const resolutions = this.collect_resolutions();
		const repointed = this.total_links(this.source);
		frappe.confirm(
			__(
				"<b>{0}</b> will be merged into <b>{1}</b>.<br><br>{2} linked document(s) will be repointed to {1}.<br><b>{0} will be deleted.</b><br><br>This cannot be undone from the UI.",
				[
					frappe.utils.escape_html(this.source),
					frappe.utils.escape_html(this.target),
					repointed,
				]
			),
			() => this.execute_merge(resolutions)
		);
	}

	async execute_merge(resolutions) {
		try {
			frappe.dom.freeze(__("Merging {0} into {1}…", [this.source, this.target]));
			await frappe.call("merge_tool.api.merge_docs", {
				doctype: this.doctype,
				source: this.source,
				target: this.target,
				resolutions: resolutions,
			});
			this.dialog.hide();
			frappe.show_alert({
				message: __("Merged {0} into {1}", [this.source, this.target]),
				indicator: "green",
			});
			frappe.set_route("Form", this.doctype, this.target);
		} finally {
			frappe.dom.unfreeze();
		}
	}
};
