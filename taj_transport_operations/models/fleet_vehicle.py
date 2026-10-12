"""Transport categories on the existing Fleet source of truth."""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..hooks import ensure_transport_categories


class FleetVehicleModelCategory(models.Model):
    _inherit = "fleet.vehicle.model.category"

    @api.model
    def _ensure_transport_categories(self):
        ensure_transport_categories(self.env)


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    is_transport_trailer = fields.Boolean(compute="_compute_is_transport_trailer")

    @api.depends("category_id")
    def _compute_is_transport_trailer(self):
        category = self.env.ref("taj_transport_operations.fleet_category_trailer",
                                raise_if_not_found=False)
        for vehicle in self:
            vehicle.is_transport_trailer = bool(category and vehicle.category_id == category)

    @api.model
    def _transport_resource_domain(self, kind):
        category = self.env.ref("taj_transport_operations.fleet_category_" + kind)
        return [("category_id", "=", category.id), ("active", "=", True)]

    def write(self, vals):
        if "company_id" in vals:
            self.check_access("write")
            changing = self.filtered(lambda v: v.company_id.id != vals["company_id"])
            if changing and self.env["taj.transport.assignment"].sudo().search_count([
                "|", ("vehicle_id", "in", changing.ids), ("trailer_id", "in", changing.ids),
            ]):
                raise ValidationError(_(
                    "The company cannot change for a truck or trailer with trip assignment history."
                ))
        result = super().write(vals)
        if {"category_id", "model_id"}.intersection(vals):
            assignments = self.env["taj.transport.assignment"].sudo()
            for kind, field in (("truck", "vehicle_id"), ("trailer", "trailer_id")):
                category = self.env.ref("taj_transport_operations.fleet_category_" + kind,
                                        raise_if_not_found=False)
                if category:
                    invalid = self.filtered(lambda v: v.category_id != category)
                    if invalid and assignments.search_count([(field, "in", invalid.ids)]):
                        raise ValidationError(_(
                            "The category/model cannot change the resource type of an assigned vehicle."
                        ))
        return result
