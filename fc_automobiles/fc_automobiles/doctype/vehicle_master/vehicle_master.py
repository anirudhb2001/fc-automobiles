# Copyright (c) 2026, FC and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate


class VehicleMaster(Document):
	def validate(self):
		self.validate_vehicle_number()
		self.validate_year()
		self.validate_model_matches_make_and_type()

	def validate_vehicle_number(self):
		self.vehicle_number = (self.vehicle_number or "").strip()
		if not self.vehicle_number:
			frappe.throw(_("Vehicle Number is mandatory."))

		existing = frappe.db.exists("Vehicle Master", self.vehicle_number)
		# Autoname is "field:vehicle_number", so by the time validate() runs,
		# a brand-new doc's self.name has ALREADY been set to this same
		# string - is_new() is what actually tells a genuine duplicate apart
		# from an in-place update of the same row.
		if existing and (self.is_new() or existing != self.name):
			frappe.throw(_("Vehicle Number already exists."))

	def validate_year(self):
		# Optional field - 0/blank both mean "not specified", not an error.
		if not self.year:
			return

		if cint(self.year) <= 0:
			frappe.throw(_("Year must be a valid positive value."))

		if cint(self.year) > getdate().year + 1:
			frappe.throw(_("Year cannot be far in the future."))

	def validate_model_matches_make_and_type(self):
		# Only enforce on a new record or when make/model/vehicle_type is
		# actually being changed. Some pre-existing Vehicle Master records
		# carry make/model values from before Vehicle Make/Vehicle Model
		# existed as real master data and don't match any such record at all
		# (they already fail to save via Frappe's own Link validation for
		# that reason) - this must not additionally block unrelated edits
		# (e.g. updating notes) on those legacy rows.
		if not (
			self.is_new()
			or self.has_value_changed("make")
			or self.has_value_changed("model")
			or self.has_value_changed("vehicle_type")
		):
			return

		if not self.model:
			return

		valid = frappe.db.exists("Vehicle Model", {
			"name": self.model,
			"make": self.make,
			"vehicle_type": self.vehicle_type,
		})
		if not valid:
			frappe.throw(_(
				"{0} is not a valid {1} model for {2}."
			).format(self.model, self.vehicle_type, self.make))

	def after_insert(self):
		from fc_automobiles.api.vehicle import log_vehicle_history

		log_vehicle_history(
			self.name,
			"Created",
			details=_("Vehicle Master created for {0}.").format(self.customer),
			source="fc_automobiles",
		)

	def on_update(self):
		self.sync_customer_vehicle_link()
		self.log_active_state_change()

	def sync_customer_vehicle_link(self):
		# Keeps the Customer Vehicle Link child rows in sync with `customer` -
		# runs on both the very first insert (previous is None below, so
		# nothing is logged - after_insert()'s "Created" event already covers
		# it) and any later reassignment of an EXISTING Vehicle Master's
		# customer field (e.g. from the Vehicle Master list/form itself).
		# Without this, the Customer Vehicle Link rows silently go stale.
		if not self.has_value_changed("customer"):
			return

		previous = self.get_doc_before_save()
		old_customer = previous.customer if previous else None

		if old_customer == self.customer:
			return

		from fc_automobiles.api.vehicle import link_vehicle_to_customer, log_vehicle_history

		if old_customer:
			from fc_automobiles.api.vehicle import CUSTOMER_VEHICLES_FIELDNAME

			frappe.db.delete("Customer Vehicle Link", {
				"parenttype": "Customer",
				"parent": old_customer,
				"parentfield": CUSTOMER_VEHICLES_FIELDNAME,
				"vehicle": self.name,
			})

		if self.customer:
			link_vehicle_to_customer(self.customer, self.name)

		if previous is not None:
			log_vehicle_history(
				self.name,
				"Ownership Changed",
				details=_("Customer changed from {0} to {1}.").format(old_customer, self.customer),
				source="fc_automobiles",
			)

	def log_active_state_change(self):
		if not self.has_value_changed("active"):
			return

		# previous is None on the very first insert - after_insert()'s
		# "Created" event already covers that, so nothing extra to log here.
		if self.get_doc_before_save() is None:
			return

		from fc_automobiles.api.vehicle import log_vehicle_history

		log_vehicle_history(
			self.name,
			"Activated" if self.active else "Deactivated",
			source="fc_automobiles",
		)
