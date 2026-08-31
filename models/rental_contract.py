import base64
import hashlib
import json

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class RentalContract(models.Model):
    _name = "rental.contract"
    _description = "Rental Contract"
    _inherit = ["portal.mixin", "mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    name = fields.Char(default=lambda self: _("New"), copy=False, readonly=True, index=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("sent", "Awaiting Signature"),
            ("signed", "Signed"),
            ("active", "Active"),
            ("ended", "Ended"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", store=True, readonly=True
    )
    unit_id = fields.Many2one(
        "rental.unit", required=True, tracking=True, check_company=True, index=True
    )
    property_id = fields.Many2one(related="unit_id.property_id", store=True, index=True)
    partner_id = fields.Many2one(
        "res.partner", string="Tenant", required=True, tracking=True, index=True
    )
    manager_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user, required=True, tracking=True
    )
    application_id = fields.Many2one(
        "rental.application", string="Tenant Application", tracking=True,
        check_company=True, ondelete="restrict", copy=False,
        domain="[('state', '=', 'approved'), ('company_id', '=', company_id)]",
    )
    template_id = fields.Many2one(
        "rental.contract.template",
        string="Contract Template",
        tracking=True,
        check_company=True,
        domain="[('active', '=', True), ('company_id', '=', company_id)]",
        ondelete="restrict",
    )
    template_name = fields.Char(string="Applied Template", readonly=True, copy=True)
    template_version = fields.Char(
        string="Applied Template Version", readonly=True, copy=True, tracking=True
    )
    template_source_hash = fields.Char(readonly=True, copy=True)
    rental_type = fields.Selection(
        [("short", "Short Term"), ("long", "Long Term")],
        default="long",
        required=True,
        tracking=True,
    )
    date_start = fields.Date(required=True, tracking=True)
    date_end = fields.Date(required=True, tracking=True)
    billing_frequency = fields.Selection(
        [
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
            ("quarterly", "Quarterly"),
            ("yearly", "Yearly"),
        ],
        default="monthly",
        required=True,
        tracking=True,
    )
    rent_amount = fields.Monetary(
        string="Rent per Billing Period", required=True, currency_field="currency_id", tracking=True
    )
    deposit_amount = fields.Monetary(currency_field="currency_id", tracking=True)
    rent_product_id = fields.Many2one(
        "product.product", required=True, domain="[('type', '=', 'service')]", check_company=True
    )
    deposit_product_id = fields.Many2one(
        "product.product", domain="[('type', '=', 'service')]", check_company=True
    )
    payment_term_id = fields.Many2one("account.payment.term", check_company=True)
    invoice_lead_days = fields.Integer(
        default=0, help="Create each invoice this many days before its rental period starts."
    )
    terms = fields.Html(required=True, default=lambda self: self._default_terms())
    billing_line_ids = fields.One2many("rental.billing.line", "contract_id", copy=False)
    charge_ids = fields.One2many("rental.charge", "contract_id", string="Tenant Charges", copy=False)
    invoice_ids = fields.One2many("account.move", "rental_contract_id", string="Invoices")
    deposit_invoice_id = fields.Many2one(
        "account.move", string="Deposit Invoice", copy=False, readonly=True, check_company=True
    )
    invoice_count = fields.Integer(compute="_compute_invoice_count")
    scheduled_rent_total = fields.Monetary(
        compute="_compute_scheduled_total", currency_field="currency_id"
    )
    pending_charge_total = fields.Monetary(
        compute="_compute_charge_totals", currency_field="currency_id"
    )
    charge_count = fields.Integer(compute="_compute_charge_totals")
    late_fee_enabled = fields.Boolean(string="Apply Late Fees")
    late_fee_calculation = fields.Selection(
        [("fixed", "Fixed Amount"), ("percent", "Percentage of Outstanding Balance")],
        default="fixed", required=True,
    )
    late_fee_amount = fields.Monetary(currency_field="currency_id")
    late_fee_percentage = fields.Float(string="Late Fee %")
    late_fee_grace_days = fields.Integer(string="Grace Period (Days)", default=5)
    late_fee_product_id = fields.Many2one(
        "product.product", domain="[('type', '=', 'service')]", check_company=True
    )
    tenant_signature = fields.Binary(copy=False, attachment=True)
    tenant_signed_by = fields.Char(copy=False, readonly=True)
    tenant_signed_on = fields.Datetime(copy=False, readonly=True)
    tenant_sign_ip = fields.Char(copy=False, readonly=True)
    tenant_sign_user_agent = fields.Char(copy=False, readonly=True)
    tenant_signature_hash = fields.Char(copy=False, readonly=True)
    require_manager_signature = fields.Boolean(default=True)
    manager_signature = fields.Binary(copy=False, attachment=True)
    manager_signed_by = fields.Char(copy=False, readonly=True)
    manager_signed_on = fields.Datetime(copy=False, readonly=True)
    manager_signature_hash = fields.Char(copy=False, readonly=True)
    signed_snapshot_json = fields.Text(copy=False, readonly=True)
    signed_document_hash = fields.Char(copy=False, readonly=True, index=True)
    final_document_attachment_id = fields.Many2one(
        "ir.attachment", copy=False, readonly=True, ondelete="restrict"
    )
    final_document_hash = fields.Char(copy=False, readonly=True)
    finalized_on = fields.Datetime(copy=False, readonly=True)
    amends_contract_id = fields.Many2one(
        "rental.contract", string="Amends Contract", copy=False, readonly=True, ondelete="restrict"
    )
    amendment_ids = fields.One2many("rental.contract", "amends_contract_id", string="Amendments")
    termination_date = fields.Date(copy=False, tracking=True)
    termination_reason = fields.Text(copy=False, tracking=True)

    _WORKFLOW_FIELDS = {
        "state", "tenant_signature", "tenant_signed_by", "tenant_signed_on", "tenant_sign_ip",
        "tenant_sign_user_agent", "tenant_signature_hash", "manager_signed_by", "manager_signed_on",
        "manager_signature_hash", "signed_snapshot_json", "signed_document_hash",
        "final_document_attachment_id", "final_document_hash", "finalized_on", "access_token",
        "deposit_invoice_id",
    }
    _LEGAL_FIELDS = {
        "company_id", "unit_id", "partner_id", "manager_id", "template_id", "template_name",
        "template_version", "template_source_hash", "rental_type", "date_start", "date_end",
        "billing_frequency", "rent_amount", "deposit_amount", "rent_product_id",
        "deposit_product_id", "payment_term_id", "invoice_lead_days", "terms",
        "billing_line_ids", "require_manager_signature", "amends_contract_id",
        "late_fee_enabled", "late_fee_calculation", "late_fee_amount",
        "late_fee_percentage", "late_fee_grace_days", "late_fee_product_id",
        "application_id",
    }

    _application_contract_uniq = models.Constraint(
        "unique(application_id)",
        "A prospective tenant application can only create one rental contract.",
    )

    @api.model
    def _default_terms(self):
        return _(
            "<p>The tenant agrees to use the premises responsibly, pay rent according to the "
            "billing schedule, and return the unit in the condition required by applicable law. "
            "House rules, utilities, cancellation terms, deposit treatment, and notice periods "
            "should be completed here before requesting signatures.</p>"
        )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            supplied_audit_fields = self._WORKFLOW_FIELDS.difference({"state"}).intersection(vals)
            if not self.env.su and (
                vals.get("state", "draft") != "draft"
                or any(vals.get(field_name) for field_name in supplied_audit_fields)
            ):
                raise AccessError(_("Workflow and signature audit fields cannot be set when creating a contract."))
            if vals.get("template_id"):
                template = self.env["rental.contract.template"].browse(vals["template_id"])
                for field_name, value in template._get_contract_values().items():
                    vals.setdefault(field_name, value)
            unit = self.env["rental.unit"].browse(vals.get("unit_id")) if vals.get("unit_id") else False
            if unit:
                for field_name, value in self._get_unit_pricing_values(unit, vals.get("rental_type", "long")).items():
                    vals.setdefault(field_name, value)
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("rental.contract") or _("New")
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            protected = self._WORKFLOW_FIELDS.intersection(vals)
            if protected:
                raise AccessError(_("Workflow and signature audit fields can only be changed by contract actions."))
            if "manager_signature" in vals:
                if not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
                    raise AccessError(_("Only rental managers can provide the manager signature."))
                if any(c.state not in ("sent", "signed") or c.manager_signed_on for c in self):
                    raise UserError(_("The manager signature cannot be changed in the current state."))
            legal = self._LEGAL_FIELDS.intersection(vals)
            if legal and any(contract.state != "draft" for contract in self):
                raise UserError(_("Signed or confirmed contracts are immutable. Create an amendment instead."))
        return super().write(vals)

    def unlink(self):
        if any(c.state not in ("draft", "cancelled") or c.tenant_signed_on or c.manager_signed_on for c in self):
            raise UserError(_("Signed, active, or historical contracts cannot be deleted."))
        return super().unlink()

    @api.model
    def _get_unit_pricing_values(self, unit, rental_type):
        rent_product = (
            unit.rent_product_id
            or self.env["rental.unit"]._ensure_default_rent_product()
        )
        return {
            "company_id": unit.company_id.id,
            "rent_product_id": rent_product.id,
            "deposit_product_id": unit.deposit_product_id.id,
            "deposit_amount": unit.deposit_amount,
            "rent_amount": unit.short_term_rate if rental_type == "short" else unit.long_term_rate,
        }

    @api.onchange("template_id")
    def _onchange_template_id(self):
        if self.template_id:
            self.update(self.template_id._get_contract_values())
            if self.unit_id:
                frequency = self.template_id.billing_frequency
                self.update(self._get_unit_pricing_values(self.unit_id, self.template_id.rental_type))
                self.billing_frequency = frequency

    @api.onchange("unit_id", "rental_type")
    def _onchange_unit_id(self):
        if not self.unit_id:
            return
        self.company_id = self.unit_id.company_id
        self.rent_product_id = (
            self.unit_id.rent_product_id
            or self.env["rental.unit"]._ensure_default_rent_product()
        )
        self.deposit_product_id = self.unit_id.deposit_product_id
        self.deposit_amount = self.unit_id.deposit_amount
        if self.rental_type == "short":
            self.billing_frequency = "daily"
            self.rent_amount = self.unit_id.short_term_rate
        else:
            self.billing_frequency = "monthly"
            self.rent_amount = self.unit_id.long_term_rate
        if self.template_id:
            self.billing_frequency = self.template_id.billing_frequency

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for contract in self:
            if contract.date_start and contract.date_end and contract.date_end < contract.date_start:
                raise ValidationError(_("The end date must be on or after the start date."))

    @api.constrains("rent_amount", "deposit_amount", "invoice_lead_days")
    def _check_positive_amounts(self):
        for contract in self:
            if contract.rent_amount <= 0:
                raise ValidationError(_("Rent must be greater than zero."))
            if contract.deposit_amount < 0 or contract.invoice_lead_days < 0:
                raise ValidationError(_("Deposit and invoice lead days cannot be negative."))

    @api.constrains(
        "late_fee_enabled", "late_fee_calculation", "late_fee_amount",
        "late_fee_percentage", "late_fee_grace_days", "late_fee_product_id",
    )
    def _check_late_fee_policy(self):
        for contract in self:
            if contract.late_fee_grace_days < 0 or contract.late_fee_amount < 0 or contract.late_fee_percentage < 0:
                raise ValidationError(_("Late-fee amounts, percentages, and grace days cannot be negative."))
            if contract.late_fee_enabled and not contract.late_fee_product_id:
                raise ValidationError(_("Select a late-fee service product when late fees are enabled."))
            if contract.late_fee_enabled and contract.late_fee_calculation == "fixed" and not contract.late_fee_amount:
                raise ValidationError(_("Enter a fixed late-fee amount."))
            if contract.late_fee_enabled and contract.late_fee_calculation == "percent" and not contract.late_fee_percentage:
                raise ValidationError(_("Enter a late-fee percentage."))

    @api.constrains("unit_id", "date_start", "date_end", "state")
    def _check_overlapping_contracts(self):
        blocking_states = ("sent", "signed", "active")
        for contract in self.filtered(
            lambda item: item.unit_id and item.date_start and item.date_end and item.state in blocking_states
        ):
            overlap = self.search_count(
                [
                    ("id", "!=", contract.id),
                    ("unit_id", "=", contract.unit_id.id),
                    ("state", "in", blocking_states),
                    ("date_start", "<=", contract.date_end),
                    ("date_end", ">=", contract.date_start),
                ],
                limit=1,
            )
            if overlap:
                raise ValidationError(_("This unit already has an overlapping reserved contract."))

    @api.constrains("unit_id", "company_id")
    def _check_unit_company(self):
        for contract in self:
            if contract.unit_id and contract.unit_id.company_id != contract.company_id:
                raise ValidationError(_("The contract and rental unit must belong to the same company."))

    @api.constrains("application_id", "partner_id", "unit_id")
    def _check_approved_application(self):
        for contract in self.filtered("application_id"):
            if contract.application_id.state != "approved":
                raise ValidationError(_("Only an approved prospective tenant application can be linked to a contract."))
            if contract.application_id.partner_id != contract.partner_id:
                raise ValidationError(_("The contract tenant must match the approved application."))
            if contract.application_id.unit_id and contract.application_id.unit_id != contract.unit_id:
                raise ValidationError(_("The contract unit must match the approved application."))

    @api.depends("invoice_ids")
    def _compute_invoice_count(self):
        for contract in self:
            contract.invoice_count = len(contract.invoice_ids)

    @api.depends("billing_line_ids.amount", "billing_line_ids.state")
    def _compute_scheduled_total(self):
        for contract in self:
            contract.scheduled_rent_total = sum(
                contract.billing_line_ids.filtered(lambda line: line.state != "cancelled").mapped("amount")
            )

    @api.depends("charge_ids.amount", "charge_ids.state")
    def _compute_charge_totals(self):
        for contract in self:
            active_charges = contract.charge_ids.filtered(lambda charge: charge.state != "cancelled")
            contract.charge_count = len(active_charges)
            contract.pending_charge_total = sum(
                active_charges.filtered(lambda charge: charge.state == "pending").mapped("amount")
            )

    def _compute_access_url(self):
        super()._compute_access_url()
        for contract in self:
            contract.access_url = f"/my/rental-contracts/{contract.id}"

    def _get_report_base_filename(self):
        self.ensure_one()
        return f"Rental Contract - {self.name}"

    def _period_delta(self):
        self.ensure_one()
        return {
            "daily": relativedelta(days=1),
            "weekly": relativedelta(weeks=1),
            "monthly": relativedelta(months=1),
            "quarterly": relativedelta(months=3),
            "yearly": relativedelta(years=1),
        }[self.billing_frequency]

    def _prepare_schedule_commands(self):
        self.ensure_one()
        commands = [Command.clear()]
        period_start = self.date_start
        delta = self._period_delta()
        while period_start <= self.date_end:
            regular_end = period_start + delta - relativedelta(days=1)
            period_end = min(regular_end, self.date_end)
            regular_days = (regular_end - period_start).days + 1
            actual_days = (period_end - period_start).days + 1
            amount = self.currency_id.round(self.rent_amount * actual_days / regular_days)
            commands.append(
                Command.create(
                    {
                        "date_from": period_start,
                        "date_to": period_end,
                        "invoice_date": period_start - relativedelta(days=self.invoice_lead_days),
                        "amount": amount,
                    }
                )
            )
            period_start = regular_end + relativedelta(days=1)
        return commands

    def action_confirm(self):
        if not self.env.user.has_group("apartment_rental_management.group_rental_user") and not self.env.su:
            raise AccessError(_("Only rental users can confirm contracts."))
        for contract in self.sorted(lambda c: (c.unit_id.id, c.id)):
            if contract.state != "draft":
                continue
            self.env.cr.execute("SELECT pg_advisory_xact_lock(%s, %s)", (728641, contract.unit_id.id))
            if contract.template_id:
                contract.template_id._check_can_apply(contract)
            contract._check_overlapping_contracts()
            contract.sudo().write({"billing_line_ids": contract._prepare_schedule_commands()})
            contract.sudo()._portal_ensure_token()
            contract.sudo().write({"state": "sent"})
            contract.message_post(body=_("Contract confirmed and prepared for tenant signature."))
        return True

    def action_apply_template(self):
        for contract in self:
            if contract.state != "draft":
                raise UserError(_("Templates can only be applied to draft contracts."))
            if not contract.template_id:
                raise UserError(_("Select a contract template first."))
            contract.template_id._check_can_apply(contract)
            values = contract.template_id._get_contract_values()
            values.update(self._get_unit_pricing_values(contract.unit_id, contract.template_id.rental_type))
            contract.write(values)
            contract.message_post(
                body=_(
                    "Applied contract template %(template)s, version %(version)s.",
                    template=contract.template_id.name,
                    version=contract.template_id.version,
                )
            )
        return True

    def action_send_for_signature(self):
        self.ensure_one()
        if self.state == "draft":
            self.action_confirm()
        if self.state != "sent":
            raise UserError(_("Only a contract awaiting signature can be sent."))
        if not self.partner_id.email:
            raise UserError(_("Set an email address on the tenant before sending the contract."))
        template = self.env.ref(
            "apartment_rental_management.mail_template_rental_contract_signature",
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(self.id, force_send=True)
        self.message_post(body=_("Signature request sent to the tenant."))
        return True

    def action_manager_sign(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can sign for the landlord."))
        for contract in self:
            if contract.state not in ("sent", "signed") or contract.manager_signed_on:
                raise UserError(_("This contract is not available for a manager signature."))
            if not contract.manager_signature:
                raise UserError(_("Draw or upload the manager signature first."))
            document_hash = contract._ensure_signing_snapshot()
            signed_on = fields.Datetime.now()
            signature_bytes = (
                contract.manager_signature.encode()
                if isinstance(contract.manager_signature, str)
                else contract.manager_signature
            )
            digest = hashlib.sha256(
                signature_bytes + str(signed_on).encode() + document_hash.encode()
            ).hexdigest()
            contract.sudo().write(
                {
                    "manager_signed_by": self.env.user.name,
                    "manager_signed_on": signed_on,
                    "manager_signature_hash": digest,
                }
            )
            contract.message_post(body=_("Contract signed by manager %s.", self.env.user.name))
            if contract.tenant_signed_on:
                contract._render_and_archive_final_contract()
        return True

    def action_activate(self):
        for contract in self:
            if contract.state != "signed":
                raise UserError(_("Only a signed contract can be activated."))
            if not contract.tenant_signature:
                raise UserError(_("The tenant must sign before the contract can be activated."))
            if contract.require_manager_signature and not contract.manager_signed_on:
                raise UserError(_("The manager must sign before the contract can be activated."))
            if not contract.final_document_attachment_id:
                contract._render_and_archive_final_contract()
            contract.sudo().write({"state": "active"})
            contract.message_post(body=_("Rental contract activated."))
        return True

    def action_end(self):
        for contract in self.filtered(lambda item: item.state == "active"):
            contract.sudo().write({"state": "ended", "termination_date": fields.Date.context_today(self)})
            contract.billing_line_ids.filtered(lambda line: line.state == "pending").sudo().write({"state": "cancelled"})
        return True

    def action_cancel(self):
        for contract in self.filtered(lambda item: item.state not in ("ended", "cancelled")):
            contract.sudo().write({"state": "cancelled"})
            contract.billing_line_ids.filtered(lambda line: line.state == "pending").sudo().write({"state": "cancelled"})
        return True

    def action_reset_to_draft(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can reset a contract."))
        for contract in self.filtered(lambda item: item.state == "cancelled"):
            contract.sudo().write(
                {
                    "state": "draft",
                    "tenant_signature": False,
                    "tenant_signed_by": False,
                    "tenant_signed_on": False,
                    "manager_signature": False,
                    "manager_signed_by": False,
                    "manager_signed_on": False,
                    "tenant_signature_hash": False,
                    "manager_signature_hash": False,
                    "tenant_sign_ip": False,
                    "tenant_sign_user_agent": False,
                    "signed_snapshot_json": False,
                    "signed_document_hash": False,
                    "final_document_attachment_id": False,
                    "final_document_hash": False,
                    "finalized_on": False,
                    "access_token": False,
                }
            )
            contract.sudo()._portal_ensure_token()
        return True

    def action_create_amendment(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can create amendments."))
        amendment = self.sudo().copy({
            "name": _("New"), "state": "draft", "amends_contract_id": self.id,
            "billing_line_ids": [Command.clear()], "tenant_signature": False,
            "manager_signature": False, "access_token": False,
        })
        return {"type": "ir.actions.act_window", "res_model": self._name, "view_mode": "form", "res_id": amendment.id}

    def _canonical_signing_payload(self):
        self.ensure_one()
        return {
            "contract": self.name, "company": [self.company_id.id, self.company_id.name],
            "tenant": [self.partner_id.id, self.partner_id.name, self.partner_id.contact_address],
            "unit": [self.unit_id.id, self.unit_id.display_name, self.property_id.display_name],
            "template": [self.template_name, self.template_version, self.template_source_hash],
            "rental_type": self.rental_type, "date_start": str(self.date_start), "date_end": str(self.date_end),
            "billing_frequency": self.billing_frequency, "rent_amount": str(self.rent_amount),
            "deposit_amount": str(self.deposit_amount), "currency": self.currency_id.name,
            "rent_product_id": self.rent_product_id.id, "deposit_product_id": self.deposit_product_id.id,
            "invoice_lead_days": self.invoice_lead_days, "terms": self.terms or "",
            "require_manager_signature": self.require_manager_signature,
            "late_fee_policy": {
                "enabled": self.late_fee_enabled,
                "calculation": self.late_fee_calculation,
                "amount": str(self.late_fee_amount),
                "percentage": str(self.late_fee_percentage),
                "grace_days": self.late_fee_grace_days,
                "product_id": self.late_fee_product_id.id,
            },
            "schedule": sorted([
                [str(l.date_from), str(l.date_to), str(l.invoice_date), str(l.amount)]
                for l in self.billing_line_ids if l.state != "cancelled"
            ]),
        }

    def _ensure_signing_snapshot(self):
        self.ensure_one()
        snapshot = json.dumps(self._canonical_signing_payload(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(snapshot.encode()).hexdigest()
        if self.signed_document_hash and self.signed_document_hash != digest:
            raise UserError(_("The contract content no longer matches its signing snapshot."))
        if not self.signed_document_hash:
            self.sudo().write({"signed_snapshot_json": snapshot, "signed_document_hash": digest})
        return digest

    def _render_and_archive_final_contract(self, author_id=None):
        self.ensure_one()
        if not self.tenant_signed_on or (self.require_manager_signature and not self.manager_signed_on):
            raise UserError(_("All required signatures must be recorded before finalizing the PDF."))
        self._ensure_signing_snapshot()
        pdf = self.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "apartment_rental_management.action_report_rental_contract", [self.id]
        )[0]
        attachment = self.env["ir.attachment"].sudo().create({
            "name": f"{self.name} - Final Signed.pdf", "type": "binary",
            "datas": base64.b64encode(pdf), "mimetype": "application/pdf",
            "res_model": self._name, "res_id": self.id,
        })
        self.sudo().write({
            "final_document_attachment_id": attachment.id,
            "final_document_hash": hashlib.sha256(pdf).hexdigest(),
            "finalized_on": fields.Datetime.now(),
        })
        self.message_post(
            body=_("Final signed contract archived."), author_id=author_id,
            attachment_ids=attachment.ids, message_type="comment", subtype_xmlid="mail.mt_comment",
        )
        return attachment

    def _record_tenant_signature(self, signature, signer_name, ip_address=None, user_agent=None, author_id=None):
        self.ensure_one()
        if self.state != "sent" or self.tenant_signed_on:
            raise UserError(_("This contract is not awaiting a tenant signature."))
        document_hash = self._ensure_signing_snapshot()
        signed_on = fields.Datetime.now()
        signature_bytes = signature.encode() if isinstance(signature, str) else signature
        digest = hashlib.sha256(
            signature_bytes + str(signed_on).encode() + document_hash.encode()
        ).hexdigest()
        self.sudo().write({
            "tenant_signature": signature, "tenant_signed_by": signer_name,
            "tenant_signed_on": signed_on, "tenant_sign_ip": ip_address,
            "tenant_sign_user_agent": (user_agent or "")[:512],
            "tenant_signature_hash": digest, "state": "signed",
        })
        self.message_post(
            body=_("Contract signed by tenant %s.", signer_name), author_id=author_id,
            message_type="comment", subtype_xmlid="mail.mt_comment",
        )
        if not self.require_manager_signature or self.manager_signed_on:
            self._render_and_archive_final_contract(author_id=author_id)
        return True

    def _invoice_line_values(self, product, amount, description):
        self.ensure_one()
        taxes = product.taxes_id.filtered(
            lambda tax: not tax.company_id or tax.company_id == self.company_id
        )
        return {
            "product_id": product.id,
            "name": description,
            "quantity": 1,
            "price_unit": amount,
            "tax_ids": [Command.set(taxes.ids)],
        }

    def _create_invoice_for_schedule(self, schedule):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can create invoices."))
        if schedule.state != "pending":
            return schedule.move_id
        if self.state not in ("signed", "active", "ended"):
            raise UserError(_("Invoices can only be created for signed or active contracts."))
        description = _(
            "Rent %(unit)s — %(start)s to %(end)s",
            unit=self.unit_id.display_name,
            start=fields.Date.to_string(schedule.date_from),
            end=fields.Date.to_string(schedule.date_to),
        )
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company_id.id,
                "partner_id": self.partner_id.id,
                "invoice_date": schedule.invoice_date,
                "invoice_origin": self.name,
                "invoice_payment_term_id": self.payment_term_id.id,
                "rental_contract_id": self.id,
                "rental_billing_line_id": schedule.id,
                "invoice_line_ids": [
                    Command.create(
                        self._invoice_line_values(self.rent_product_id, schedule.amount, description)
                    )
                ],
            }
        )
        schedule.sudo().write({"state": "invoiced", "move_id": move.id})
        self.message_post(body=_("Rent invoice %s created.", move.display_name))
        return move

    def _create_invoice_for_charge(self, charge):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can create tenant-charge invoices."))
        if charge.contract_id != self:
            raise UserError(_("The tenant charge does not belong to this contract."))
        if charge.state != "pending":
            return charge.move_id
        if self.state not in ("signed", "active", "ended"):
            raise UserError(_("Tenant charges can only be invoiced for signed, active, or ended contracts."))
        if charge.amount <= 0:
            raise UserError(_("The tenant charge must have an amount greater than zero."))
        line_values = self._invoice_line_values(charge.product_id, charge.unit_price, charge.name)
        line_values["quantity"] = charge.quantity
        move = self.env["account.move"].create({
            "move_type": "out_invoice", "company_id": self.company_id.id,
            "partner_id": self.partner_id.id, "invoice_date": charge.invoice_date,
            "invoice_origin": self.name, "invoice_payment_term_id": self.payment_term_id.id,
            "rental_contract_id": self.id, "rental_charge_id": charge.id,
            "ref": _("Tenant charge: %s", charge.name),
            "invoice_line_ids": [Command.create(line_values)],
        })
        charge.sudo().write({"state": "invoiced", "move_id": move.id})
        self.message_post(body=_("Tenant-charge invoice %s created for %s.", move.display_name, charge.name))
        return move

    def action_create_due_charges(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can create tenant-charge invoices."))
        invoices = self.env["account.move"]
        today = fields.Date.context_today(self)
        for contract in self:
            for charge in contract.charge_ids.filtered(
                lambda item: item.state == "pending" and item.invoice_date <= today
            ):
                invoices |= contract._create_invoice_for_charge(charge)
        if len(invoices) == 1:
            return {"type": "ir.actions.act_window", "res_model": "account.move", "view_mode": "form", "res_id": invoices.id}
        return self.action_view_invoices() if invoices else True

    def _generate_late_fee_charges(self, today=None):
        today = today or fields.Date.context_today(self)
        Charge = self.env["rental.charge"].sudo()
        Move = self.env["account.move"].sudo()
        created = Charge
        for contract in self.filtered(lambda item: item.late_fee_enabled and item.state in ("active", "ended")):
            cutoff = today - relativedelta(days=contract.late_fee_grace_days)
            eligible = Move.search([
                ("rental_contract_id", "=", contract.id),
                ("move_type", "=", "out_invoice"),
                ("state", "=", "posted"),
                ("payment_state", "not in", ("paid", "reversed")),
                ("amount_residual", ">", 0),
                ("invoice_date_due", "<", cutoff),
            ]).filtered(lambda move: not move.rental_charge_id or move.rental_charge_id.category != "late_fee")
            for move in eligible:
                if Charge.search_count([("source_invoice_id", "=", move.id)], limit=1):
                    continue
                amount = (
                    contract.late_fee_amount
                    if contract.late_fee_calculation == "fixed"
                    else contract.currency_id.round(move.amount_residual * contract.late_fee_percentage / 100.0)
                )
                if amount <= 0:
                    continue
                created |= Charge.create({
                    "name": _("Late fee for overdue invoice %s", move.name),
                    "contract_id": contract.id, "category": "late_fee",
                    "calculation_type": "fixed", "product_id": contract.late_fee_product_id.id,
                    "service_date": today, "invoice_date": today, "unit_price": amount,
                    "source_invoice_id": move.id,
                    "notes": _("Automatically generated after a %s-day grace period.", contract.late_fee_grace_days),
                })
        return created

    def action_create_due_invoices(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can create invoices."))
        invoices = self.env["account.move"]
        today = fields.Date.context_today(self)
        for contract in self:
            for schedule in contract.billing_line_ids.filtered(
                lambda line: line.state == "pending" and line.invoice_date <= today
            ):
                invoices |= contract._create_invoice_for_schedule(schedule)
        if len(invoices) == 1:
            return {
                "type": "ir.actions.act_window",
                "res_model": "account.move",
                "view_mode": "form",
                "res_id": invoices.id,
            }
        return self.action_view_invoices() if invoices else True

    def action_create_deposit_invoice(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can create deposit invoices."))
        if self.state not in ("signed", "active"):
            raise UserError(_("Deposit invoices can only be created for signed or active contracts."))
        if self.deposit_invoice_id:
            raise UserError(_("A deposit invoice has already been created for this contract."))
        if not self.deposit_amount or not self.deposit_product_id:
            raise UserError(_("Set both a deposit amount and a deposit product first."))
        move = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company_id.id,
                "partner_id": self.partner_id.id,
                "invoice_date": fields.Date.context_today(self),
                "invoice_origin": self.name,
                "rental_contract_id": self.id,
                "invoice_payment_term_id": self.payment_term_id.id,
                "invoice_line_ids": [
                    Command.create(
                        self._invoice_line_values(
                            self.deposit_product_id,
                            self.deposit_amount,
                            _("Security deposit — %s", self.unit_id.display_name),
                        )
                    )
                ],
            }
        )
        self.sudo().write({"deposit_invoice_id": move.id})
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": move.id,
        }

    def action_view_invoices(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental managers can view rental accounting records."))
        action = self.env["ir.actions.actions"]._for_xml_id("account.action_move_out_invoice_type")
        action["domain"] = [("rental_contract_id", "=", self.id)]
        action["context"] = {"default_move_type": "out_invoice", "default_rental_contract_id": self.id}
        return action

    def action_view_charges(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "apartment_rental_management.action_rental_charge"
        )
        action["domain"] = [("contract_id", "=", self.id)]
        action["context"] = {"default_contract_id": self.id}
        return action

    @api.model
    def _cron_create_due_invoices(self):
        today = fields.Date.context_today(self)
        lines = self.env["rental.billing.line"].search(
            [
                ("state", "=", "pending"),
                ("invoice_date", "<=", today),
                ("contract_id.state", "=", "active"),
            ]
        )
        for line in lines:
            line.contract_id.sudo()._create_invoice_for_schedule(line)
        active_contracts = self.search([("state", "=", "active")])
        active_contracts.sudo()._generate_late_fee_charges(today)
        due_charges = self.env["rental.charge"].search([
            ("state", "=", "pending"), ("invoice_date", "<=", today),
            ("contract_id.state", "in", ("active", "ended")),
        ])
        for charge in due_charges:
            charge.contract_id.sudo()._create_invoice_for_charge(charge)
        expired = self.search([("state", "=", "active"), ("date_end", "<", today)])
        expired.action_end()
