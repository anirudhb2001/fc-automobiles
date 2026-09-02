import frappe
from frappe import _
from frappe.utils import cint, getdate


def log_vehicle_history(
	vehicle,
	event_type,
	event_date=None,
	km=None,
	details=None,
	reference_doctype=None,
	reference_name=None,
	source=None,
):
	"""Append one Vehicle History row. Never edits or removes existing rows.

	Importable by any app (tyre_management, global_tyres, ...) that wants to
	record a generic vehicle-level event without fc_automobiles knowing
	anything about the caller's own doctypes - reference_doctype/reference_name
	is a plain Dynamic Link, not validated against a fixed list.
	"""
	frappe.get_doc({
		"doctype": "Vehicle History",
		"vehicle": vehicle,
		"event_type": event_type,
		"event_date": getdate(event_date) if event_date else getdate(),
		"km": km,
		"details": details,
		"reference_doctype": reference_doctype,
		"reference_name": reference_name,
		"source": source,
	}).insert(ignore_permissions=True)


@frappe.whitelist()
def get_vehicle_history(vehicle, event_type=None):
	frappe.has_permission("Vehicle Master", "read", doc=vehicle, throw=True)

	filters = {"vehicle": vehicle}
	if event_type:
		filters["event_type"] = event_type

	return frappe.get_all(
		"Vehicle History",
		filters=filters,
		fields=["name", "event_date", "event_type", "km", "source", "details", "reference_doctype", "reference_name"],
		order_by="event_date desc, creation desc",
	)


def get_or_create_vehicle(customer, vehicle_number, vehicle_type=None, make=None, model=None, year=None, notes=None):
	"""Resolve vehicle_number to a Vehicle Master, creating it if it doesn't
	exist yet. Never reassigns ownership - if the vehicle belongs to a
	different customer, this raises instead of silently switching it over.

	Pure vehicle-master resolution only - does not touch any Customer's
	`vehicles` child table (see link_vehicle_to_customer for that).
	"""
	vehicle_number = (vehicle_number or "").strip()
	if not vehicle_number:
		frappe.throw(_("Vehicle Number is mandatory."))

	existing_name = frappe.db.exists("Vehicle Master", vehicle_number)
	if existing_name:
		owner = frappe.db.get_value("Vehicle Master", existing_name, "customer")
		if owner != customer:
			frappe.throw(_("This vehicle is already registered to another customer."))
		return existing_name

	vehicle_doc = frappe.get_doc({
		"doctype": "Vehicle Master",
		"vehicle_number": vehicle_number,
		"customer": customer,
		"vehicle_type": vehicle_type,
		"make": make,
		"model": model,
		"year": year or None,
		"notes": notes,
	})
	vehicle_doc.insert()
	return vehicle_doc.name


CUSTOMER_VEHICLES_FIELDNAME = "fc_vehicles"


def link_vehicle_to_customer(customer, vehicle_master_name):
	"""Add a Customer Vehicle Link row pointing at `vehicle_master_name` onto
	`customer`, without loading and re-saving the whole Customer document -
	callers that already sit inside a Customer save cycle (e.g. a doc_events
	hook on Customer) can call this directly without recursing back into it.
	"""
	already_linked = frappe.db.exists("Customer Vehicle Link", {
		"parenttype": "Customer",
		"parent": customer,
		"parentfield": CUSTOMER_VEHICLES_FIELDNAME,
		"vehicle": vehicle_master_name,
	})
	if already_linked:
		return

	vehicle_doc = frappe.get_doc("Vehicle Master", vehicle_master_name)
	next_idx = frappe.db.count("Customer Vehicle Link", {
		"parenttype": "Customer",
		"parent": customer,
		"parentfield": CUSTOMER_VEHICLES_FIELDNAME,
	}) + 1

	frappe.get_doc({
		"doctype": "Customer Vehicle Link",
		"parenttype": "Customer",
		"parent": customer,
		"parentfield": CUSTOMER_VEHICLES_FIELDNAME,
		"idx": next_idx,
		"vehicle": vehicle_doc.name,
		"vehicle_number": vehicle_doc.vehicle_number,
		"vehicle_type": vehicle_doc.vehicle_type,
		"make": vehicle_doc.make,
		"model": vehicle_doc.model,
		"year": vehicle_doc.year,
		"active": vehicle_doc.active,
	}).insert(ignore_permissions=True)


@frappe.whitelist()
def add_vehicle_to_customer(customer, vehicle_number, vehicle_type=None, make=None, model=None, year=None, notes=None):
	"""Find-or-create the Vehicle Master for `vehicle_number` and link it onto
	`customer`'s Vehicles table.

	Returns the Vehicle Master's fields (not just its name) so a caller like
	a Service Job Card can populate its own vehicle fields without a second
	round-trip.
	"""
	vehicle_master_name = get_or_create_vehicle(
		customer, vehicle_number, vehicle_type, make, model, year, notes
	)
	link_vehicle_to_customer(customer, vehicle_master_name)

	return frappe.get_doc("Vehicle Master", vehicle_master_name).as_dict()


@frappe.whitelist()
def get_customer_vehicles(customer):
	"""Fetch all active Vehicle Master records linked to a customer."""
	return frappe.get_all(
		"Vehicle Master",
		filters={"customer": customer, "active": 1},
		fields=["name", "vehicle_number", "vehicle_type", "make", "model", "year"],
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def make_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link query for Vehicle Master's `make` field - restricts to Vehicle
	Makes that have at least one active Vehicle Model of the selected
	vehicle_type. Vehicle Make itself carries no vehicle_type field (a make
	like "Honda" can span multiple vehicle types via its Models), so this is
	computed from Vehicle Model data rather than a stored field.

	With no vehicle_type given, returns nothing - the client is expected to
	ask the user to pick Vehicle Type first (see vehicle_master.js).
	"""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)

	vehicle_type = (filters or {}).get("vehicle_type")
	if not vehicle_type:
		return []

	return frappe.db.sql(
		"""
		select vm.name, vm.make_name
		from `tabVehicle Make` vm
		where vm.active = 1
			and (vm.name like %(txt)s or vm.make_name like %(txt)s)
			and exists (
				select 1 from `tabVehicle Model` vmod
				where vmod.make = vm.name
					and vmod.vehicle_type = %(vehicle_type)s
					and vmod.active = 1
			)
		order by vm.name
		limit %(page_len)s offset %(start)s
		""",
		{
			"txt": f"%{txt}%",
			"vehicle_type": vehicle_type,
			"start": start,
			"page_len": page_len,
		},
	)


@frappe.whitelist()
def is_make_valid_for_vehicle_type(make, vehicle_type):
	"""Whether `make` has at least one active Vehicle Model of `vehicle_type` -
	used by vehicle_master.js to decide whether to clear an already-selected
	Make when Vehicle Type changes."""
	if not make or not vehicle_type:
		return False

	return bool(frappe.db.exists("Vehicle Model", {
		"make": make,
		"vehicle_type": vehicle_type,
		"active": 1,
	}))


SERVICE_HISTORY_PAGE_SIZE = 15


@frappe.whitelist()
def get_service_history(vehicle_master, page=1, page_size=SERVICE_HISTORY_PAGE_SIZE):
	"""Vehicle-wise service history, computed live from submitted Service Job
	Cards - nothing is stored on Vehicle Master itself, so there's no stale
	value to worry about on cancel/amend (a cancelled Job Card just drops out
	of this query the next time it runs).

	Only Job Cards linked via the `vehicle_master` field are considered, so
	this naturally only ever surfaces Job Cards created after that field
	existed - old, unlinked Job Cards are excluded without any migration.

	Server-side paginated: only `page_size` rows (plus one, to cheaply detect
	whether a next page exists without a separate COUNT query) are ever
	fetched for a given page, newest first. `summary` is computed from its
	own lightweight query independent of the requested page, so it always
	reflects the vehicle's full history even when viewing an older page.

	Generic by design - reads only Service Job Card / Service Job Card Item /
	Employee, all of which are business-agnostic. No tyre_management or
	global_tyres specific logic belongs here.

	"Service Job Card" itself is currently defined by global_tyres, not
	fc_automobiles - a client running fc_automobiles alone (no global_tyres)
	won't have that table. Degrade to an empty history instead of a SQL error
	in that case, the same way tyre_management checks for frappe_whatsapp
	before using it, so fc_automobiles keeps working standalone.
	"""
	frappe.has_permission("Vehicle Master", "read", doc=vehicle_master, throw=True)

	if not frappe.db.table_exists("Service Job Card"):
		return {
			"history": [],
			"tyre_events": {},
			"summary": {"last_service_date": None, "current_km": None, "total_visits": 0, "last_job_card": None},
			"page": 1,
			"page_size": cint(page_size) or SERVICE_HISTORY_PAGE_SIZE,
			"has_previous": False,
			"has_next": False,
		}

	page = cint(page) or 1
	page_size = cint(page_size) or SERVICE_HISTORY_PAGE_SIZE
	start = (page - 1) * page_size

	# Fetch one extra row past this page's worth to know whether a next page
	# exists; it's dropped below before returning.
	rows = frappe.db.sql(
		"""
		select
			sjc.name as job_card,
			sjc.posting_date,
			sjc.current_km_reading,
			sjci.item_code,
			sjci.item_name,
			sjci.service_type,
			sjci.is_service,
			sjci.is_tyre,
			sjci.km_at_change,
			sjci.wheel_position,
			sjci.qty,
			sjci.technician,
			emp.employee_name as technician_name
		from `tabService Job Card` sjc
		inner join `tabService Job Card Item` sjci on sjci.parent = sjc.name
		left join `tabEmployee` emp on emp.name = sjci.technician
		where sjc.vehicle_master = %(vehicle_master)s and sjc.docstatus = 1
		order by sjc.posting_date desc, sjc.creation desc, sjci.idx asc
		limit %(limit)s offset %(offset)s
		""",
		{"vehicle_master": vehicle_master, "limit": page_size + 1, "offset": start},
		as_dict=1,
	)

	has_next = len(rows) > page_size
	rows = rows[:page_size]

	# Tyre-specific detail (position, action, brand) is NOT stored on Service
	# Job Card Item - wheel_position/km_at_change above are deprecated and
	# unpopulated for current Job Cards (see that doctype's field
	# descriptions). The real source of truth is tyre_management's Tyre
	# History, one row per (job card, tyre position), linked back via
	# reference_doctype/reference_name. Optional dependency: degrade to no
	# tyre_events (unchanged historical behaviour) if tyre_management isn't
	# installed, exactly like the Service Job Card table_exists guard above.
	tyre_events_by_job_card = {}
	if frappe.db.table_exists("Tyre History"):
		job_card_names = list({row.job_card for row in rows})
		if job_card_names:
			tyre_rows = frappe.db.sql(
				"""
				select
					th.reference_name as job_card,
					th.tyre_position,
					th.action,
					th.event_date,
					th.event_km,
					th.tyre_item,
					th.source,
					it.item_name as tyre_item_name,
					it.brand as tyre_brand
				from `tabTyre History` th
				left join `tabItem` it on it.name = th.tyre_item
				where th.reference_doctype = 'Service Job Card'
					and th.reference_name in %(job_cards)s
				order by th.creation asc
				""",
				{"job_cards": job_card_names},
				as_dict=1,
			)

			# Qty isn't on Tyre History either - it's on the Service Job Card
			# Item row for the same tyre_item/job card, so borrow it from
			# there rather than inventing a new field.
			qty_by_job_card_item = {(row.job_card, row.item_code): row.qty for row in rows}

			for tyre_row in tyre_rows:
				tyre_row["qty"] = qty_by_job_card_item.get((tyre_row.job_card, tyre_row.tyre_item))
				tyre_events_by_job_card.setdefault(tyre_row.job_card, []).append(tyre_row)

	summary_row = frappe.db.sql(
		"""
		select sjc.name as job_card, sjc.posting_date, sjc.current_km_reading
		from `tabService Job Card` sjc
		where sjc.vehicle_master = %(vehicle_master)s and sjc.docstatus = 1
		order by sjc.posting_date desc, sjc.creation desc
		limit 1
		""",
		{"vehicle_master": vehicle_master},
		as_dict=1,
	)
	total_visits = frappe.db.count("Service Job Card", {"vehicle_master": vehicle_master, "docstatus": 1})

	summary = {
		"last_service_date": summary_row[0].posting_date if summary_row else None,
		"current_km": summary_row[0].current_km_reading if summary_row else None,
		"total_visits": total_visits,
		"last_job_card": summary_row[0].job_card if summary_row else None,
	}

	return {
		"history": rows,
		"tyre_events": tyre_events_by_job_card,
		"summary": summary,
		"page": page,
		"page_size": page_size,
		"has_previous": page > 1,
		"has_next": has_next,
	}
