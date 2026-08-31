import base64
import hashlib
import logging
from datetime import timedelta

from markupsafe import escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_amount, format_date


_logger = logging.getLogger(__name__)


class RentalNoticeTemplate(models.Model):
    _name = "rental.notice.template"
    _description = "Rental Notice Template"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "notice_type, name, version desc"

    name = fields.Char(required=True, tracking=True)
    version = fields.Char(required=True, default="1", tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    notice_type = fields.Selection(
        [("late_payment", "Late-Payment Letter"), ("eviction", "Eviction Notice")],
        required=True, default="late_payment", tracking=True, index=True,
    )
    default_ground = fields.Selection(
        [
            ("nonpayment", "Nonpayment of Rent"),
            ("repeated_nonpayment", "Repeated Consecutive Nonpayment"),
            ("contract_violation", "Contract-Clause Violation"),
            ("other", "Other Lawful Ground"),
        ],
        required=True, default="nonpayment", tracking=True,
    )
    cure_days = fields.Integer(
        string="Response / Cure Days", default=5,
        help="Default number of calendar days after the notice date for the tenant to respond or cure.",
    )
    delivery_method = fields.Selection(
        [
            ("email", "Email"),
            ("hand", "Hand Delivery"),
            ("registered_mail", "Registered Mail"),
            ("courier", "Courier"),
            ("portal", "Tenant Portal"),
            ("other", "Other"),
        ],
        required=True, default="email",
    )
    effective_date = fields.Date()
    expiration_date = fields.Date()
    subject = fields.Char(required=True, translate=True)
    body_html = fields.Html(required=True, translate=True, sanitize=True)
    internal_notes = fields.Html()
    notice_ids = fields.One2many("rental.notice", "template_id")
    notice_count = fields.Integer(compute="_compute_notice_count")

    _name_version_company_uniq = models.Constraint(
        "unique(name, version, company_id)",
        "A notice template name and version must be unique per company.",
    )

    @api.constrains("cure_days", "effective_date", "expiration_date")
    def _check_policy(self):
        for template in self:
            if template.cure_days < 0:
                raise ValidationError(_("Response or cure days cannot be negative."))
            if (
                template.effective_date and template.expiration_date
                and template.expiration_date < template.effective_date
            ):
                raise ValidationError(_("The expiration date cannot precede the effective date."))

    @api.constrains("notice_type", "default_ground")
    def _check_notice_ground(self):
        for template in self:
            if template.notice_type == "late_payment" and template.default_ground != "nonpayment":
                raise ValidationError(_("Late-payment templates must use the nonpayment ground."))

    @api.depends("notice_ids")
    def _compute_notice_count(self):
        for template in self:
            template.notice_count = len(template.notice_ids)

    def _check_can_apply(self, contract, notice_date=None):
        self.ensure_one()
        notice_date = notice_date or fields.Date.context_today(self)
        if self.company_id != contract.company_id:
            raise UserError(_("The notice template and contract must belong to the same company."))
        if self.effective_date and notice_date < self.effective_date:
            raise UserError(_("This notice template is not yet effective."))
        if self.expiration_date and notice_date > self.expiration_date:
            raise UserError(_("This notice template has expired."))

    def action_new_revision(self):
        self.ensure_one()
        try:
            next_version = str(int(self.version) + 1)
        except ValueError:
            next_version = f"{self.version}.1"
        while self.search_count([
            ("name", "=", self.name), ("version", "=", next_version),
            ("company_id", "=", self.company_id.id),
        ]):
            next_version = f"{next_version}.1"
        revision = self.copy({"version": next_version})
        return {
            "type": "ir.actions.act_window", "res_model": self._name,
            "view_mode": "form", "res_id": revision.id,
        }

    def action_view_notices(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "apartment_rental_management.action_rental_notice"
        )
        action["domain"] = [("template_id", "=", self.id)]
        action["context"] = {"default_template_id": self.id, "default_notice_type": self.notice_type}
        return action

    def write(self, vals):
        legal_fields = {
            "name", "version", "company_id", "notice_type", "default_ground",
            "cure_days", "delivery_method", "effective_date", "expiration_date",
            "subject", "body_html",
        }
        if self.notice_ids and legal_fields.intersection(vals):
            raise UserError(_("A notice template already used by a notice is immutable. Create a new revision instead."))
        return super().write(vals)

    def unlink(self):
        if self.notice_ids:
            raise UserError(_("A notice template used by a notice cannot be deleted. Archive it instead."))
        return super().unlink()


class RentalContractViolation(models.Model):
    _name = "rental.contract.violation"
    _description = "Rental Contract Violation"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "incident_date desc, id desc"

    name = fields.Char(required=True, tracking=True)
    contract_id = fields.Many2one(
        "rental.contract", required=True, ondelete="restrict", check_company=True, index=True
    )
    company_id = fields.Many2one(related="contract_id.company_id", store=True, index=True)
    partner_id = fields.Many2one(related="contract_id.partner_id", store=True, string="Tenant")
    clause_reference = fields.Char(
        required=True, tracking=True,
        help="Identify the signed contract clause or house rule allegedly violated.",
    )
    incident_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    description = fields.Text(required=True, tracking=True)
    severity = fields.Selection(
        [("minor", "Minor"), ("material", "Material"), ("serious", "Serious")],
        required=True, default="material", tracking=True,
    )
    state = fields.Selection(
        [
            ("open", "Open"), ("cured", "Cured"), ("escalated", "Escalated"),
            ("dismissed", "Dismissed"),
        ],
        required=True, default="open", tracking=True, index=True,
    )
    reported_by_id = fields.Many2one(
        "res.users", readonly=True, default=lambda self: self.env.user, copy=False
    )
    reported_on = fields.Datetime(readonly=True, default=fields.Datetime.now, copy=False)
    resolution_notes = fields.Text(tracking=True)
    notice_ids = fields.Many2many(
        "rental.notice", "rental_notice_violation_rel", "violation_id", "notice_id",
        string="Related Notices", readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(
            vals.get("state", "open") != "open" or vals.get("reported_by_id") or vals.get("reported_on")
            for vals in vals_list
        ):
            raise AccessError(_("Violation audit fields can only be set by the rental workflow."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if {"state", "reported_by_id", "reported_on", "contract_id"}.intersection(vals):
                raise AccessError(_("Violation audit fields can only be changed by workflow actions."))
            if any(violation.state not in ("open", "escalated") for violation in self):
                raise UserError(_("Resolved or dismissed violations cannot be edited."))
        return super().write(vals)

    def unlink(self):
        if self.notice_ids or any(violation.state != "open" for violation in self):
            raise UserError(_("Referenced or processed violations cannot be deleted."))
        return super().unlink()

    def _set_state(self, state):
        if not self.env.su and not self.env.user.has_group(
            "apartment_rental_management.group_rental_manager"
        ):
            raise AccessError(_("Only rental administrators can resolve or escalate violations."))
        self.sudo().write({"state": state})
        return True

    def action_mark_cured(self):
        return self._set_state("cured")

    def action_escalate(self):
        return self._set_state("escalated")

    def action_dismiss(self):
        return self._set_state("dismissed")

    def action_reopen(self):
        return self._set_state("open")


class RentalNotice(models.Model):
    _name = "rental.notice"
    _description = "Tenant Rental Notice"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "notice_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False, index=True)
    state = fields.Selection(
        [
            ("draft", "Draft"), ("approved", "Reviewed"), ("issued", "Issued"),
            ("delivered", "Delivered"), ("cancelled", "Cancelled"),
        ],
        required=True, default="draft", tracking=True, index=True,
    )
    notice_type = fields.Selection(
        [("late_payment", "Late-Payment Letter"), ("eviction", "Eviction Notice")],
        required=True, default="late_payment", tracking=True, index=True,
    )
    contract_id = fields.Many2one(
        "rental.contract", required=True, ondelete="restrict", check_company=True,
        tracking=True, index=True,
    )
    company_id = fields.Many2one(related="contract_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="contract_id.currency_id", store=True)
    partner_id = fields.Many2one(related="contract_id.partner_id", store=True, string="Tenant")
    unit_id = fields.Many2one(related="contract_id.unit_id", store=True)
    template_id = fields.Many2one(
        "rental.notice.template", required=True, ondelete="restrict", check_company=True,
        domain="[('notice_type', '=', notice_type), ('active', '=', True), ('company_id', '=', company_id)]",
        tracking=True,
    )
    template_name = fields.Char(readonly=True, copy=True)
    template_version = fields.Char(readonly=True, copy=True)
    ground = fields.Selection(
        [
            ("nonpayment", "Nonpayment of Rent"),
            ("repeated_nonpayment", "Repeated Consecutive Nonpayment"),
            ("contract_violation", "Contract-Clause Violation"),
            ("other", "Other Lawful Ground"),
        ],
        required=True, default="nonpayment", tracking=True,
    )
    grounds_description = fields.Text(
        help="Additional factual description. Do not use this field as a substitute for local legal review."
    )
    source_invoice_ids = fields.Many2many(
        "account.move", "rental_notice_invoice_rel", "notice_id", "move_id",
        string="Overdue Rent Invoices",
        domain="[('rental_contract_id', '=', contract_id), ('rental_billing_line_id', '!=', False)]",
    )
    violation_ids = fields.Many2many(
        "rental.contract.violation", "rental_notice_violation_rel", "notice_id", "violation_id",
        string="Contract Violations", domain="[('contract_id', '=', contract_id)]",
    )
    notice_date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    response_deadline = fields.Date(string="Response / Cure Deadline", tracking=True)
    amount_due = fields.Monetary(currency_field="currency_id", readonly=True, copy=True)
    consecutive_missed_payments = fields.Integer(readonly=True, copy=True)
    subject = fields.Char(readonly=True, copy=True)
    body_html = fields.Html(readonly=True, copy=True, sanitize=True)
    eligibility_snapshot = fields.Text(readonly=True, copy=True)
    delivery_method = fields.Selection(
        [
            ("email", "Email"), ("hand", "Hand Delivery"),
            ("registered_mail", "Registered Mail"), ("courier", "Courier"),
            ("portal", "Tenant Portal"), ("other", "Other"),
        ],
        required=True, default="email", tracking=True,
    )
    reviewed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    reviewed_on = fields.Datetime(readonly=True, copy=False)
    issued_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    issued_on = fields.Datetime(readonly=True, copy=False)
    delivered_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    delivered_on = fields.Datetime(readonly=True, copy=False)
    delivery_reference = fields.Char(copy=False, tracking=True)
    delivery_notes = fields.Text(copy=False)
    delivery_proof = fields.Binary(attachment=True, copy=False)
    delivery_proof_filename = fields.Char(copy=False)
    document_attachment_id = fields.Many2one(
        "ir.attachment", readonly=True, copy=False, ondelete="restrict"
    )
    document_hash = fields.Char(readonly=True, copy=False, index=True)

    _AUDIT_FIELDS = {
        "state", "template_name", "template_version", "amount_due",
        "consecutive_missed_payments", "subject", "body_html", "eligibility_snapshot",
        "reviewed_by_id", "reviewed_on", "issued_by_id", "issued_on",
        "delivered_by_id", "delivered_on", "document_attachment_id", "document_hash",
    }

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(
            vals.get("state", "draft") != "draft"
            or self._AUDIT_FIELDS.difference({"state"}).intersection(vals)
            for vals in vals_list
        ):
            raise AccessError(_("Notice workflow and audit fields cannot be set during creation."))
        for values in vals_list:
            if not values.get("name") or values["name"] == _("New"):
                values["name"] = self.env["ir.sequence"].next_by_code("rental.notice") or _("New")
            template = self.env["rental.notice.template"].browse(values.get("template_id"))
            if template:
                values.setdefault("notice_type", template.notice_type)
                values.setdefault("ground", template.default_ground)
                values.setdefault("delivery_method", template.delivery_method)
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if self._AUDIT_FIELDS.intersection(vals):
                raise AccessError(_("Notice workflow and audit fields can only be changed by notice actions."))
            if any(notice.state != "draft" for notice in self):
                allowed_delivery = {
                    "delivery_method", "delivery_reference", "delivery_notes",
                    "delivery_proof", "delivery_proof_filename",
                }
                if set(vals).difference(allowed_delivery):
                    raise UserError(_("Only delivery evidence can be edited after review."))
        return super().write(vals)

    def unlink(self):
        if any(notice.state not in ("draft", "cancelled") for notice in self):
            raise UserError(_("Reviewed, issued, or delivered notices cannot be deleted."))
        return super().unlink()

    @api.onchange("template_id")
    def _onchange_template_id(self):
        if self.template_id:
            self.notice_type = self.template_id.notice_type
            self.ground = self.template_id.default_ground
            self.delivery_method = self.template_id.delivery_method
            self.response_deadline = self.notice_date + timedelta(days=self.template_id.cure_days)

    @api.onchange("notice_date")
    def _onchange_notice_date(self):
        if self.template_id and self.notice_date:
            self.response_deadline = self.notice_date + timedelta(days=self.template_id.cure_days)

    @api.constrains("notice_type", "ground")
    def _check_ground(self):
        for notice in self:
            if notice.notice_type == "late_payment" and notice.ground != "nonpayment":
                raise ValidationError(_("Late-payment letters must use the nonpayment ground."))

    @api.constrains("contract_id", "template_id", "source_invoice_ids", "violation_ids")
    def _check_related_records(self):
        for notice in self:
            if notice.template_id and notice.template_id.company_id != notice.company_id:
                raise ValidationError(_("The notice template and contract must belong to the same company."))
            if notice.template_id and notice.template_id.notice_type != notice.notice_type:
                raise ValidationError(_("The notice and template types must match."))
            if any(move.rental_contract_id != notice.contract_id for move in notice.source_invoice_ids):
                raise ValidationError(_("Every source invoice must belong to this rental contract."))
            if any(violation.contract_id != notice.contract_id for violation in notice.violation_ids):
                raise ValidationError(_("Every violation must belong to this rental contract."))

    def _check_manager(self):
        if not self.env.su and not self.env.user.has_group(
            "apartment_rental_management.group_rental_manager"
        ):
            raise AccessError(_("Only rental administrators can review or issue tenant notices."))

    def _eligibility_values(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        if self.contract_id.state != "active":
            raise UserError(_("Notices can only be prepared for active rental contracts."))
        self.template_id._check_can_apply(self.contract_id, self.notice_date or today)

        eligible = self.contract_id._eligible_overdue_rent_invoices(today)
        selected_invoices = self.source_invoice_ids or eligible
        if self.ground in ("nonpayment", "repeated_nonpayment"):
            if not selected_invoices:
                raise UserError(_("No unpaid rent invoice is beyond the configured grace period."))
            invalid = selected_invoices - eligible
            if invalid:
                raise UserError(_("Every selected rent invoice must remain unpaid beyond the grace period."))

        streak = self.contract_id._consecutive_missed_rent_invoices(today)
        if self.ground == "repeated_nonpayment":
            threshold = self.contract_id.eviction_nonpayment_threshold
            if len(streak) < threshold:
                raise UserError(_(
                    "This contract has %(actual)s consecutive missed payment(s); %(required)s are required.",
                    actual=len(streak), required=threshold,
                ))
            selected_invoices = streak

        selected_violations = self.violation_ids
        if self.ground == "contract_violation":
            if not selected_violations:
                raise UserError(_("Select at least one recorded contract-clause violation."))
            resolved = selected_violations.filtered(lambda item: item.state in ("cured", "dismissed"))
            if resolved:
                raise UserError(_("Cured or dismissed violations cannot support a new notice."))
        if self.ground == "other" and not self.grounds_description:
            raise UserError(_("Describe the factual and lawful ground for this notice."))

        amount_due = sum(selected_invoices.mapped("amount_residual"))
        return {
            "source_invoice_ids": [Command.set(selected_invoices.ids)],
            "violation_ids": [Command.set(selected_violations.ids)],
            "amount_due": amount_due,
            "consecutive_missed_payments": len(streak),
            "response_deadline": (
                (self.notice_date or today) + timedelta(days=self.template_id.cure_days)
            ),
            "eligibility_snapshot": self._eligibility_summary(
                selected_invoices, selected_violations, len(streak), today
            ),
        }

    def _eligibility_summary(self, invoices, violations, streak, checked_on):
        invoice_rows = [
            "%s | due %s | residual %s" % (
                move.name, move.invoice_date_due, move.amount_residual
            )
            for move in invoices
        ]
        violation_rows = [
            "%s | clause %s | incident %s | %s" % (
                violation.name, violation.clause_reference,
                violation.incident_date, violation.state,
            )
            for violation in violations
        ]
        return "\n".join([
            f"Eligibility checked: {checked_on}",
            f"Grace days: {self.contract_id.payment_notice_grace_days}",
            f"Consecutive missed payments: {streak}",
            "Invoices:", *(invoice_rows or ["None"]),
            "Violations:", *(violation_rows or ["None"]),
        ])

    def _placeholder_values(self):
        self.ensure_one()
        invoices = ", ".join(self.source_invoice_ids.mapped("name")) or _("None")
        clauses = ", ".join(self.violation_ids.mapped("clause_reference")) or _("None")
        violation_details = "; ".join(
            f"{item.name}: {item.description}" for item in self.violation_ids
        ) or _("None")
        ground_label = dict(self._fields["ground"].selection).get(self.ground, self.ground)
        values = {
            "tenant_name": self.partner_id.name or "",
            "tenant_address": self.partner_id.contact_address or "",
            "contract_number": self.contract_id.name or "",
            "unit": self.unit_id.display_name or "",
            "property": self.contract_id.property_id.display_name or "",
            "company_name": self.company_id.name or "",
            "notice_date": format_date(self.env, self.notice_date) if self.notice_date else "",
            "response_deadline": (
                format_date(self.env, self.response_deadline) if self.response_deadline else ""
            ),
            "amount_due": format_amount(self.env, self.amount_due, self.currency_id),
            "invoice_references": invoices,
            "consecutive_missed_payments": self.consecutive_missed_payments,
            "grounds": self.grounds_description or ground_label,
            "clause_references": clauses,
            "violation_details": violation_details,
        }
        return {key: str(escape(value)) for key, value in values.items()}

    def _render_template_text(self, source):
        rendered = source or ""
        for key, value in self._placeholder_values().items():
            rendered = rendered.replace("{{ %s }}" % key, value)
            rendered = rendered.replace("{{%s}}" % key, value)
        return rendered

    def action_approve(self):
        self._check_manager()
        for notice in self:
            if notice.state != "draft":
                raise UserError(_("Only draft notices can be reviewed."))
            values = notice._eligibility_values()
            values.update({
                "template_name": notice.template_id.name,
                "template_version": notice.template_id.version,
                "reviewed_by_id": self.env.user.id,
                "reviewed_on": fields.Datetime.now(),
                "state": "approved",
            })
            notice.sudo().write(values)
            notice.sudo().write({
                "subject": notice._render_template_text(notice.template_id.subject),
                "body_html": notice._render_template_text(notice.template_id.body_html),
            })
            notice.message_post(body=_("Notice eligibility reviewed and content frozen."))
        return True

    def _archive_pdf(self):
        self.ensure_one()
        if self.document_attachment_id:
            return self.document_attachment_id
        pdf = self.env["ir.actions.report"].sudo()._render_qweb_pdf(
            "apartment_rental_management.action_report_rental_notice", [self.id]
        )[0]
        attachment = self.env["ir.attachment"].sudo().create({
            "name": f"{self.name} - {self.subject}.pdf",
            "type": "binary", "datas": base64.b64encode(pdf),
            "mimetype": "application/pdf", "res_model": self._name, "res_id": self.id,
        })
        self.sudo().write({
            "document_attachment_id": attachment.id,
            "document_hash": hashlib.sha256(pdf).hexdigest(),
        })
        self.message_post(
            body=_("Issued notice PDF archived."), attachment_ids=attachment.ids,
            message_type="comment", subtype_xmlid="mail.mt_comment",
        )
        return attachment

    def _send_notice_email(self):
        self.ensure_one()
        if not self.partner_id.email:
            raise UserError(_("Set an email address on the tenant before email delivery."))
        attachment = self._archive_pdf()
        mail_template = self.env.ref(
            "apartment_rental_management.mail_template_rental_notice",
            raise_if_not_found=False,
        )
        if not mail_template:
            raise UserError(_("The rental notice email template is not available."))
        mail_template.send_mail(
            self.id, force_send=True,
            email_values={"attachment_ids": [Command.link(attachment.id)]},
        )
        self.sudo().write({
            "state": "delivered", "delivered_by_id": self.env.user.id,
            "delivered_on": fields.Datetime.now(), "delivery_method": "email",
        })
        self.message_post(body=_("Notice emailed to %s.", self.partner_id.email))

    def action_issue(self):
        self._check_manager()
        for notice in self:
            if notice.state != "approved":
                raise UserError(_("Only reviewed notices can be issued."))
            current = notice._eligibility_values()
            current_invoice_ids = set(current["source_invoice_ids"][0][2])
            current_violation_ids = set(current["violation_ids"][0][2])
            eligibility_changed = (
                current_invoice_ids != set(notice.source_invoice_ids.ids)
                or current_violation_ids != set(notice.violation_ids.ids)
                or current["consecutive_missed_payments"] != notice.consecutive_missed_payments
                or notice.currency_id.compare_amounts(current["amount_due"], notice.amount_due)
            )
            if eligibility_changed:
                raise UserError(_(
                    "The balance, payment streak, or supporting violations changed after review. "
                    "Reset the notice to draft and review it again."
                ))
            notice.sudo().write({
                "state": "issued", "issued_by_id": self.env.user.id,
                "issued_on": fields.Datetime.now(),
            })
            notice._archive_pdf()
            notice.message_post(body=_("Notice formally issued by %s.", self.env.user.name))
            if notice.delivery_method == "email":
                notice._send_notice_email()
        return True

    def action_mark_delivered(self):
        self._check_manager()
        for notice in self:
            if notice.state != "issued":
                raise UserError(_("Only issued notices can be marked delivered."))
            if notice.delivery_method != "email" and not (
                notice.delivery_reference or notice.delivery_proof or notice.delivery_notes
            ):
                raise UserError(_("Record a delivery reference, proof, or delivery note first."))
            notice.sudo().write({
                "state": "delivered", "delivered_by_id": self.env.user.id,
                "delivered_on": fields.Datetime.now(),
            })
            notice.message_post(body=_("Notice delivery recorded."))
        return True

    def action_cancel(self):
        self._check_manager()
        for notice in self.filtered(lambda item: item.state in ("draft", "approved", "issued")):
            notice.sudo().write({"state": "cancelled"})
        return True

    def action_reset_to_draft(self):
        self._check_manager()
        for notice in self:
            if notice.state != "approved":
                raise UserError(_("Only reviewed, unissued notices can be reset."))
            notice.sudo().write({
                "state": "draft", "reviewed_by_id": False, "reviewed_on": False,
                "template_name": False, "template_version": False,
                "amount_due": 0, "consecutive_missed_payments": 0,
                "subject": False, "body_html": False, "eligibility_snapshot": False,
            })
        return True

    def action_preview_pdf(self):
        self.ensure_one()
        return self.env.ref(
            "apartment_rental_management.action_report_rental_notice"
        ).report_action(self)


class RentalContractNotice(models.Model):
    _inherit = "rental.contract"

    payment_notice_grace_days = fields.Integer(
        string="Payment Notice Grace Days", default=5,
        help="An unpaid posted rent invoice becomes notice-eligible after this many calendar days.",
    )
    eviction_nonpayment_threshold = fields.Integer(
        string="Consecutive Missed Payments for Eviction Review", default=3,
    )
    late_notice_template_id = fields.Many2one(
        "rental.notice.template", string="Late-Payment Template", ondelete="restrict",
        domain="[('notice_type', '=', 'late_payment'), ('active', '=', True), ('company_id', '=', company_id)]",
        check_company=True,
    )
    eviction_notice_template_id = fields.Many2one(
        "rental.notice.template", string="Eviction Notice Template", ondelete="restrict",
        domain="[('notice_type', '=', 'eviction'), ('active', '=', True), ('company_id', '=', company_id)]",
        check_company=True,
    )
    auto_prepare_late_notices = fields.Boolean(
        help="The daily rental job prepares reviewable drafts; it never issues notices automatically."
    )
    notice_ids = fields.One2many("rental.notice", "contract_id", copy=False)
    violation_ids = fields.One2many("rental.contract.violation", "contract_id", copy=False)
    notice_count = fields.Integer(compute="_compute_notice_counts")
    violation_count = fields.Integer(compute="_compute_notice_counts")

    @api.depends("notice_ids", "violation_ids")
    def _compute_notice_counts(self):
        for contract in self:
            contract.notice_count = len(contract.notice_ids)
            contract.violation_count = len(contract.violation_ids)

    @api.constrains("payment_notice_grace_days", "eviction_nonpayment_threshold")
    def _check_notice_policy(self):
        for contract in self:
            if contract.payment_notice_grace_days < 0:
                raise ValidationError(_("Payment notice grace days cannot be negative."))
            if contract.eviction_nonpayment_threshold < 2:
                raise ValidationError(_("The consecutive nonpayment threshold must be at least two."))

    def write(self, vals):
        policy_fields = {
            "payment_notice_grace_days", "eviction_nonpayment_threshold",
            "late_notice_template_id", "eviction_notice_template_id",
        }
        if (
            not self.env.su and policy_fields.intersection(vals)
            and any(contract.state != "draft" for contract in self)
        ):
            raise UserError(_("Notice policy is part of the draft contract configuration and cannot be changed after confirmation."))
        return super().write(vals)

    def _canonical_signing_payload(self):
        payload = super()._canonical_signing_payload()
        payload["tenant_notice_policy"] = {
            "payment_grace_days": self.payment_notice_grace_days,
            "eviction_nonpayment_threshold": self.eviction_nonpayment_threshold,
            "late_notice_template_id": self.late_notice_template_id.id,
            "eviction_notice_template_id": self.eviction_notice_template_id.id,
        }
        return payload

    def _eligible_overdue_rent_invoices(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        cutoff = today - timedelta(days=self.payment_notice_grace_days)
        return self.env["account.move"].search([
            ("rental_contract_id", "=", self.id),
            ("rental_billing_line_id", "!=", False),
            ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
            ("payment_state", "not in", ("paid", "reversed")),
            ("amount_residual", ">", 0), ("invoice_date_due", "<", cutoff),
        ], order="invoice_date_due, id")

    def _consecutive_missed_rent_invoices(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        cutoff = today - timedelta(days=self.payment_notice_grace_days)
        due_invoices = self.env["account.move"].search([
            ("rental_contract_id", "=", self.id),
            ("rental_billing_line_id", "!=", False),
            ("move_type", "=", "out_invoice"), ("state", "=", "posted"),
            ("invoice_date_due", "<", cutoff),
        ], order="invoice_date_due desc, id desc")
        streak = self.env["account.move"]
        for move in due_invoices:
            if move.payment_state in ("paid", "reversed") or move.amount_residual <= 0:
                break
            streak |= move
        return streak.sorted(lambda move: (move.invoice_date_due, move.id))

    def _check_notice_manager(self):
        if not self.env.su and not self.env.user.has_group(
            "apartment_rental_management.group_rental_manager"
        ):
            raise AccessError(_("Only rental administrators can prepare tenant notices."))

    def action_prepare_late_notice(self):
        self.ensure_one()
        self._check_notice_manager()
        if self.state != "active":
            raise UserError(_("Late-payment notices can only be prepared for active contracts."))
        if not self.late_notice_template_id:
            raise UserError(_("Configure a late-payment notice template on the contract first."))
        eligible = self._eligible_overdue_rent_invoices()
        covered = self.env["rental.notice"].search([
            ("contract_id", "=", self.id), ("notice_type", "=", "late_payment"),
            ("state", "!=", "cancelled"),
        ]).source_invoice_ids
        eligible -= covered
        if not eligible:
            raise UserError(_("There are no newly eligible overdue rent invoices after the grace period."))
        notice = self.env["rental.notice"].create({
            "notice_type": "late_payment", "contract_id": self.id,
            "template_id": self.late_notice_template_id.id, "ground": "nonpayment",
            "source_invoice_ids": [Command.set(eligible.ids)],
        })
        return {
            "type": "ir.actions.act_window", "res_model": "rental.notice",
            "view_mode": "form", "res_id": notice.id,
        }

    def action_prepare_eviction_notice(self):
        self.ensure_one()
        self._check_notice_manager()
        if self.state != "active":
            raise UserError(_("Eviction notices can only be prepared for active contracts."))
        if not self.eviction_notice_template_id:
            raise UserError(_("Configure an eviction notice template on the contract first."))
        streak = self._consecutive_missed_rent_invoices()
        violations = self.violation_ids.filtered(lambda item: item.state in ("open", "escalated"))
        values = {
            "notice_type": "eviction", "contract_id": self.id,
            "template_id": self.eviction_notice_template_id.id,
        }
        if len(streak) >= self.eviction_nonpayment_threshold:
            values.update({
                "ground": "repeated_nonpayment",
                "source_invoice_ids": [Command.set(streak.ids)],
            })
        elif violations:
            values.update({
                "ground": "contract_violation",
                "violation_ids": [Command.set(violations.ids)],
            })
        else:
            raise UserError(_(
                "The contract does not meet the consecutive nonpayment threshold and has no unresolved violations."
            ))
        notice = self.env["rental.notice"].create(values)
        return {
            "type": "ir.actions.act_window", "res_model": "rental.notice",
            "view_mode": "form", "res_id": notice.id,
        }

    def action_view_notices(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "apartment_rental_management.action_rental_notice"
        )
        action["domain"] = [("contract_id", "=", self.id)]
        action["context"] = {"default_contract_id": self.id}
        return action

    def action_view_violations(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "apartment_rental_management.action_rental_contract_violation"
        )
        action["domain"] = [("contract_id", "=", self.id)]
        action["context"] = {"default_contract_id": self.id}
        return action

    @api.model
    def _cron_prepare_late_notice_drafts(self):
        contracts = self.search([
            ("state", "=", "active"), ("auto_prepare_late_notices", "=", True),
            ("late_notice_template_id", "!=", False),
        ])
        for contract in contracts:
            try:
                contract.sudo().action_prepare_late_notice()
            except UserError as error:
                _logger.debug("Late-notice draft not prepared for %s: %s", contract.name, error)

    @api.model
    def _cron_create_due_invoices(self):
        result = super()._cron_create_due_invoices()
        self._cron_prepare_late_notice_drafts()
        return result


class RentalContractTemplateNotice(models.Model):
    _inherit = "rental.contract.template"

    payment_notice_grace_days = fields.Integer(string="Payment Notice Grace Days", default=5)
    eviction_nonpayment_threshold = fields.Integer(
        string="Consecutive Missed Payments for Eviction Review", default=3
    )
    late_notice_template_id = fields.Many2one(
        "rental.notice.template", string="Late-Payment Template", ondelete="restrict",
        domain="[('notice_type', '=', 'late_payment'), ('active', '=', True), ('company_id', '=', company_id)]",
        check_company=True,
    )
    eviction_notice_template_id = fields.Many2one(
        "rental.notice.template", string="Eviction Notice Template", ondelete="restrict",
        domain="[('notice_type', '=', 'eviction'), ('active', '=', True), ('company_id', '=', company_id)]",
        check_company=True,
    )
    auto_prepare_late_notices = fields.Boolean()

    @api.constrains("payment_notice_grace_days", "eviction_nonpayment_threshold")
    def _check_notice_policy(self):
        for template in self:
            if template.payment_notice_grace_days < 0:
                raise ValidationError(_("Payment notice grace days cannot be negative."))
            if template.eviction_nonpayment_threshold < 2:
                raise ValidationError(_("The consecutive nonpayment threshold must be at least two."))

    def _get_contract_values(self):
        values = super()._get_contract_values()
        values.update({
            "payment_notice_grace_days": self.payment_notice_grace_days,
            "eviction_nonpayment_threshold": self.eviction_nonpayment_threshold,
            "late_notice_template_id": self.late_notice_template_id.id,
            "eviction_notice_template_id": self.eviction_notice_template_id.id,
            "auto_prepare_late_notices": self.auto_prepare_late_notices,
        })
        return values

    def write(self, vals):
        policy_fields = {
            "payment_notice_grace_days", "eviction_nonpayment_threshold",
            "late_notice_template_id", "eviction_notice_template_id",
            "auto_prepare_late_notices",
        }
        if self.contract_ids and policy_fields.intersection(vals):
            raise UserError(_("A template already used by a contract is immutable. Create a new revision instead."))
        return super().write(vals)
