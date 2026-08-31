from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    rental_contract_id = fields.Many2one(
        "rental.contract", string="Rental Contract", copy=False, index=True, check_company=True
    )
    rental_billing_line_id = fields.Many2one(
        "rental.billing.line", string="Rental Schedule Line", copy=False, index=True
    )
    rental_charge_id = fields.Many2one(
        "rental.charge", string="Tenant Charge", copy=False, index=True, check_company=True
    )

    _rental_schedule_invoice_uniq = models.Constraint(
        "unique(rental_billing_line_id)",
        "A rental billing schedule line can only be linked to one invoice.",
    )
    _rental_charge_invoice_uniq = models.Constraint(
        "unique(rental_charge_id)",
        "A tenant charge can only be linked to one invoice.",
    )
