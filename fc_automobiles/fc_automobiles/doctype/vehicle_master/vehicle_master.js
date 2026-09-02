frappe.ui.form.on('Vehicle Master', {
	refresh: function(frm) {
		set_make_query(frm);
		set_model_query(frm);

		if (frm.is_new()) return;

		if (!frm.fields_dict.service_history_html) return;

		render_service_history(frm);
	},

	vehicle_type: function(frm) {
		// Model depends on both vehicle_type and make, so any vehicle_type
		// change invalidates whatever model was picked - clear it
		// unconditionally rather than trying to guess if it still applies.
		frm.set_value('model', '');
		set_make_query(frm);
		set_model_query(frm);

		if (!frm.doc.make) return;

		frappe.call({
			method: 'fc_automobiles.api.vehicle.is_make_valid_for_vehicle_type',
			args: { make: frm.doc.make, vehicle_type: frm.doc.vehicle_type },
			callback: function(r) {
				if (!r.message) {
					frm.set_value('make', '');
				}
			}
		});
	},

	make: function(frm) {
		frm.set_value('model', '');
		set_model_query(frm);
	}
});

function set_make_query(frm) {
	// Vehicle Make carries no vehicle_type of its own (one make can span
	// multiple types via its models) - filtering is computed server-side
	// from Vehicle Model data. Empty vehicle_type -> no options, prompting
	// the user to pick Vehicle Type first.
	frm.set_query('make', function() {
		return {
			query: 'fc_automobiles.api.vehicle.make_query',
			filters: { vehicle_type: frm.doc.vehicle_type },
		};
	});
}

function set_model_query(frm) {
	// Vehicle Model now carries vehicle_type directly, so a plain filters
	// object is enough - no custom query needed. Empty make/vehicle_type
	// naturally match nothing, prompting the user to pick Make first.
	frm.set_query('model', function() {
		return {
			filters: {
				make: frm.doc.make || '',
				vehicle_type: frm.doc.vehicle_type || '',
				active: 1,
			},
		};
	});
}

function render_service_history(frm, page) {
	page = page || 1;
	let wrapper = $(frm.fields_dict.service_history_html.wrapper).empty();
	wrapper.html('<p class="text-muted">Loading service history...</p>');

	frappe.call({
		method: 'fc_automobiles.api.vehicle.get_service_history',
		args: { vehicle_master: frm.doc.name, page: page },
		callback: function(r) {
			if (!r.message) {
				wrapper.empty();
				return;
			}
			let { history, tyre_events, summary, has_previous, has_next } = r.message;
			wrapper.empty();
			wrapper.append(build_summary_html(summary));
			wrapper.append(build_history_table_html(history, tyre_events || {}));
			if (has_previous || has_next) {
				wrapper.append(build_pagination_html(page, has_previous, has_next));
				wrapper.find('.service-history-prev').on('click', () => render_service_history(frm, page - 1));
				wrapper.find('.service-history-next').on('click', () => render_service_history(frm, page + 1));
			}
		}
	});
}

function build_pagination_html(page, has_previous, has_next) {
	return `
		<div class="service-history-pagination" style="margin-top: 10px; text-align: right;">
			<button type="button" class="btn btn-default btn-xs service-history-prev" ${has_previous ? '' : 'disabled'}>
				${__('Previous')}
			</button>
			<span class="text-muted" style="margin: 0 8px;">${__('Page')} ${page}</span>
			<button type="button" class="btn btn-default btn-xs service-history-next" ${has_next ? '' : 'disabled'}>
				${__('Next')}
			</button>
		</div>
	`;
}

function build_summary_html(summary) {
	let last_service_date = summary.last_service_date ? frappe.datetime.str_to_user(summary.last_service_date) : '-';
	let current_km = summary.current_km != null ? `${summary.current_km.toLocaleString('en-IN')} KM` : '-';
	let last_job_card = summary.last_job_card
		? `<a href="/app/service-job-card/${summary.last_job_card}">${summary.last_job_card}</a>`
		: '-';

	return `
		<div class="row" style="margin-bottom: 15px;">
			<div class="col-sm-3"><b>Last Service Date</b><br>${last_service_date}</div>
			<div class="col-sm-3"><b>Current KM</b><br>${current_km}</div>
			<div class="col-sm-3"><b>Total Service Visits</b><br>${summary.total_visits}</div>
			<div class="col-sm-3"><b>Last Job Card</b><br>${last_job_card}</div>
		</div>
	`;
}

function build_history_table_html(history, tyre_events) {
	if (!history || !history.length) {
		return '<p class="text-muted">No service history yet - this vehicle has no submitted Service Job Cards linked to it.</p>';
	}

	tyre_events = tyre_events || {};
	let injected_for_job_card = new Set();

	let rows = history.map(row => {
		let events = tyre_events[row.job_card];

		if (row.is_tyre && events && events.length) {
			// Tyre History has the real per-position record of what happened
			// (position, action, actual tyre fitted) - render that instead of
			// this raw item line, once per job card, so a job card touching
			// several positions shows one clearly labelled row per position
			// rather than one ambiguous row per line item.
			if (injected_for_job_card.has(row.job_card)) return '';
			injected_for_job_card.add(row.job_card);

			return events.map(ev => {
				let tyre_label = ev.tyre_item_name || ev.tyre_item || '';
				let brand = ev.tyre_brand ? ` <span class="text-muted">(${ev.tyre_brand})</span>` : '';
				return `
					<tr>
						<td>${frappe.datetime.str_to_user(row.posting_date)}</td>
						<td><a href="/app/service-job-card/${row.job_card}">${row.job_card}</a></td>
						<td>${row.current_km_reading != null ? row.current_km_reading.toLocaleString('en-IN') : ''}</td>
						<td><b>${ev.action}</b>${tyre_label ? ': ' + tyre_label + brand : ''}</td>
						<td>${ev.tyre_position || ''}</td>
						<td>${ev.qty || ''}</td>
					</tr>
				`;
			}).join('');
		}

		// Non-tyre items, and tyre-classified items on Job Cards that predate
		// the Tyre History sync (no events recorded for them) - unchanged
		// fallback so existing history never breaks or goes blank.
		let service_label = row.service_type || row.item_name || row.item_code || '';
		let extra = [];
		if (row.is_tyre && row.wheel_position) extra.push(row.wheel_position);
		if (row.technician_name || row.technician) extra.push(row.technician_name || row.technician);
		let extra_html = extra.length ? ` <span class="text-muted">(${extra.join(', ')})</span>` : '';

		return `
			<tr>
				<td>${frappe.datetime.str_to_user(row.posting_date)}</td>
				<td><a href="/app/service-job-card/${row.job_card}">${row.job_card}</a></td>
				<td>${row.current_km_reading != null ? row.current_km_reading.toLocaleString('en-IN') : ''}</td>
				<td>${service_label}${extra_html}</td>
				<td></td>
				<td>${row.qty || ''}</td>
			</tr>
		`;
	}).join('');

	return `
		<table class="table table-bordered">
			<thead>
				<tr>
					<th>Date</th>
					<th>Job Card</th>
					<th>KM</th>
					<th>Action / Service / Item</th>
					<th>Position</th>
					<th>Qty</th>
				</tr>
			</thead>
			<tbody>${rows}</tbody>
		</table>
	`;
}
