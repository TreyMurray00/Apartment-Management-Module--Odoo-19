from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class RentalBillingLine(models.Model):
    _name = "rental.billing.line"
    _description = "Rental Billing Schedule Line"
    _order = "invoice_date, id"

    contract_id = fields.Many2one(
        "rental.contract", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one(related="contract_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="contract_id.currency_id", store=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    invoice_date = fields.Date(required=True, index=True)
    amount = fields.Monetary(required=True, currency_field="currency_id")
    state = fields.Selection(
        [("pending", "Pending"), ("invoiced", "Invoiced"), ("cancelled", "Cancelled")],
        default="pending",
        required=True,
        index=True,
    )
    move_id = fields.Many2one("account.move", string="Invoice", copy=False, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            contracts = self.env["rental.contract"].browse(
                [vals.get("contract_id") for vals in vals_list if vals.get("contract_id")]
            )
            if any(contract.state != "draft" for contract in contracts):
                raise UserError(_("Billing schedules can only be edited on draft contracts."))
            if any(vals.get("state", "pending") != "pending" or vals.get("move_id") for vals in vals_list):
                raise AccessError(_("Billing audit fields can only be changed by the rental workflow."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if {"state", "move_id"}.intersection(vals):
                raise AccessError(_("Billing audit fields can only be changed by the rental workflow."))
            if any(line.contract_id.state != "draft" for line in self):
                raise UserError(_("Billing schedules can only be edited on draft contracts."))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(line.contract_id.state != "draft" for line in self):
            raise UserError(_("Billing schedules can only be edited on draft contracts."))
        return super().unlink()

    def action_create_invoice(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can create invoices."))
        invoices = self.env["account.move"]
        for line in self.filtered(lambda item: item.state == "pending"):
            invoices |= line.contract_id._create_invoice_for_schedule(line)
        if len(invoices) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "account.move",
                "view_mode": "form",
                "res_id": invoices.id,
            }
        return True
