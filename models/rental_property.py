from odoo import fields, models


class RentalProperty(models.Model):
    _name = "rental.property"
    _description = "Rental Property"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(required=True, copy=False, index=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    manager_id = fields.Many2one(
        "res.users", string="Property Manager", default=lambda self: self.env.user, tracking=True
    )
    street = fields.Char()
    street2 = fields.Char()
    city = fields.Char()
    state_id = fields.Many2one("res.country.state")
    zip = fields.Char()
    country_id = fields.Many2one("res.country")
    notes = fields.Html()
    unit_ids = fields.One2many("rental.unit", "property_id", string="Units")
    unit_count = fields.Integer(compute="_compute_unit_count")

    _code_company_uniq = models.Constraint(
        "unique(code, company_id)", "The property code must be unique per company."
    )

    def _compute_unit_count(self):
        grouped = self.env["rental.unit"]._read_group(
            [("property_id", "in", self.ids)], ["property_id"], ["__count"]
        )
        counts = {prop.id: count for prop, count in grouped}
        for prop in self:
            prop.unit_count = counts.get(prop.id, 0)

    def action_view_units(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "apartment_rental_management.action_rental_unit"
        )
        action["domain"] = [("property_id", "=", self.id)]
        action["context"] = {"default_property_id": self.id}
        return action

