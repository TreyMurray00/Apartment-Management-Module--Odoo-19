from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class RentalCharge(models.Model):
    _name = "rental.charge"
    _description = "Tenant Rental Charge"
    _inherit = ["mail.thread"]
    _order = "invoice_date desc, id desc"

    name = fields.Char(required=True, tracking=True)
    contract_id = fields.Many2one(
        "rental.contract", required=True, ondelete="restrict", index=True, check_company=True
    )
    company_id = fields.Many2one(related="contract_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="contract_id.currency_id", store=True)
    category = fields.Selection(
        [
            ("electricity", "Electricity"),
            ("water", "Water"),
            ("gas", "Gas"),
            ("internet", "Internet / Communications"),
            ("waste", "Waste / Sewer"),
            ("utility_other", "Other Utility"),
            ("damage", "Damage / Repair"),
            ("cleaning", "Cleaning"),
            ("key_replacement", "Key / Access Replacement"),
            ("late_fee", "Late Fee"),
            ("miscellaneous", "Miscellaneous"),
        ],
        required=True,
        default="miscellaneous",
        tracking=True,
        index=True,
    )
    calculation_type = fields.Selection(
        [("fixed", "Fixed Amount"), ("quantity", "Quantity"), ("meter", "Meter Readings")],
        required=True,
        default="fixed",
    )
    product_id = fields.Many2one(
        "product.product", required=True, domain="[('type', '=', 'service')]", check_company=True
    )
    service_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    invoice_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True, index=True)
    units = fields.Float(string="Units", default=1.0, digits="Product Unit of Measure")
    meter_previous = fields.Float(string="Previous Reading")
    meter_current = fields.Float(string="Current Reading")
    quantity = fields.Float(compute="_compute_quantity", store=True, digits="Product Unit of Measure")
    unit_price = fields.Monetary(required=True, currency_field="currency_id", tracking=True)
    amount = fields.Monetary(compute="_compute_amount", store=True, currency_field="currency_id")
    notes = fields.Text(help="Usage period, meter identifier, receipt reference, or explanation.")
    state = fields.Selection(
        [("pending", "Pending"), ("invoiced", "Invoiced"), ("cancelled", "Cancelled")],
        required=True,
        default="pending",
        tracking=True,
        index=True,
    )
    move_id = fields.Many2one("account.move", string="Invoice", copy=False, readonly=True)
    source_invoice_id = fields.Many2one(
        "account.move", string="Overdue Invoice", copy=False, readonly=True, check_company=True,
        help="For an automatic late fee, the overdue invoice that generated this charge.",
    )

    _late_fee_source_uniq = models.Constraint(
        "unique(source_invoice_id)",
        "Only one automatic late fee can be generated for an overdue invoice.",
    )

    @api.depends("calculation_type", "units", "meter_previous", "meter_current")
    def _compute_quantity(self):
        for charge in self:
            if charge.calculation_type == "fixed":
                charge.quantity = 1.0
            elif charge.calculation_type == "meter":
                charge.quantity = max(charge.meter_current - charge.meter_previous, 0.0)
            else:
                charge.quantity = charge.units

    @api.depends("quantity", "unit_price")
    def _compute_amount(self):
        for charge in self:
            amount = charge.quantity * charge.unit_price
            charge.amount = charge.currency_id.round(amount) if charge.currency_id else amount

    @api.constrains("units", "meter_previous", "meter_current", "unit_price")
    def _check_measurements(self):
        for charge in self:
            if charge.unit_price < 0 or charge.units < 0:
                raise ValidationError(_("Charge quantities and prices cannot be negative."))
            if charge.calculation_type == "meter" and charge.meter_current < charge.meter_previous:
                raise ValidationError(_("The current meter reading cannot be below the previous reading."))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            if any(vals.get("state", "pending") != "pending" or vals.get("move_id") or vals.get("source_invoice_id") for vals in vals_list):
                raise AccessError(_("Charge audit fields can only be set by the rental workflow."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if {"state", "move_id", "source_invoice_id", "contract_id"}.intersection(vals):
                raise AccessError(_("Charge audit fields can only be changed by the rental workflow."))
            if any(charge.state != "pending" for charge in self):
                raise UserError(_("Only pending tenant charges can be edited."))
        return super().write(vals)

    def unlink(self):
        if any(charge.state != "pending" for charge in self):
            raise UserError(_("Invoiced or cancelled tenant charges cannot be deleted."))
        return super().unlink()

    def action_create_invoice(self):
        invoices = self.env["account.move"]
        for charge in self:
            invoices |= charge.contract_id._create_invoice_for_charge(charge)
        if len(invoices) == 1:
            return {
                "type": "ir.actions.act_window", "res_model": "account.move",
                "view_mode": "form", "res_id": invoices.id,
            }
        return True

    def action_cancel(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can cancel tenant charges."))
        pending = self.filtered(lambda charge: charge.state == "pending")
        pending.sudo().write({"state": "cancelled"})
        return True
