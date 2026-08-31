import base64
import binascii
import hashlib
from io import BytesIO
from pathlib import PurePath
import zipfile

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class RentalContractTemplate(models.Model):
    _name = "rental.contract.template"
    _description = "Rental Contract Template"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name, version desc"

    name = fields.Char(required=True, tracking=True)
    version = fields.Char(required=True, default="1", tracking=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    rental_type = fields.Selection(
        [("short", "Short Term"), ("long", "Long Term")],
        required=True,
        default="long",
        tracking=True,
    )
    billing_frequency = fields.Selection(
        [
            ("daily", "Daily"),
            ("weekly", "Weekly"),
            ("monthly", "Monthly"),
            ("quarterly", "Quarterly"),
            ("yearly", "Yearly"),
        ],
        required=True,
        default="monthly",
        tracking=True,
    )
    invoice_lead_days = fields.Integer(default=0)
    require_manager_signature = fields.Boolean(default=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
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
    effective_date = fields.Date()
    expiration_date = fields.Date()
    terms = fields.Html(
        string="Contract Body",
        required=True,
        help="Editable legal text copied into each new contract as an independent snapshot.",
    )
    source_document = fields.Binary(
        string="Uploaded Master Document",
        attachment=True,
        help="Optional PDF, DOCX, or ODT master kept for reference. The editable Contract Body "
        "is what Odoo merges into the signed PDF.",
    )
    source_document_filename = fields.Char()
    internal_notes = fields.Html()
    contract_ids = fields.One2many("rental.contract", "template_id", string="Contracts")
    contract_count = fields.Integer(compute="_compute_contract_count")

    _LEGAL_FIELDS = {
        "name", "version", "company_id", "rental_type", "billing_frequency",
        "invoice_lead_days", "require_manager_signature", "effective_date",
        "expiration_date", "terms", "source_document", "source_document_filename",
        "late_fee_enabled", "late_fee_calculation", "late_fee_amount",
        "late_fee_percentage", "late_fee_grace_days", "late_fee_product_id",
    }
    _MAX_SOURCE_SIZE = 10 * 1024 * 1024

    _name_version_company_uniq = models.Constraint(
        "unique(name, version, company_id)",
        "A contract template name and version must be unique per company.",
    )

    @api.constrains("effective_date", "expiration_date")
    def _check_dates(self):
        for template in self:
            if (
                template.effective_date
                and template.expiration_date
                and template.expiration_date < template.effective_date
            ):
                raise ValidationError(_("The expiration date cannot precede the effective date."))

    @api.constrains("invoice_lead_days")
    def _check_invoice_lead_days(self):
        if any(template.invoice_lead_days < 0 for template in self):
            raise ValidationError(_("Invoice lead days cannot be negative."))

    @api.constrains(
        "late_fee_enabled", "late_fee_calculation", "late_fee_amount",
        "late_fee_percentage", "late_fee_grace_days", "late_fee_product_id",
    )
    def _check_late_fee_policy(self):
        for template in self:
            if template.late_fee_grace_days < 0 or template.late_fee_amount < 0 or template.late_fee_percentage < 0:
                raise ValidationError(_("Late-fee amounts, percentages, and grace days cannot be negative."))
            if template.late_fee_enabled and not template.late_fee_product_id:
                raise ValidationError(_("Select a late-fee service product when late fees are enabled."))
            if template.late_fee_enabled and template.late_fee_calculation == "fixed" and not template.late_fee_amount:
                raise ValidationError(_("Enter a fixed late-fee amount."))
            if template.late_fee_enabled and template.late_fee_calculation == "percent" and not template.late_fee_percentage:
                raise ValidationError(_("Enter a late-fee percentage."))

    @api.constrains("source_document", "source_document_filename")
    def _check_source_document(self):
        allowed_extensions = {".pdf", ".docx", ".odt"}
        for template in self.filtered("source_document"):
            extension = PurePath(template.source_document_filename or "").suffix.lower()
            if extension not in allowed_extensions:
                raise ValidationError(_("The uploaded master must be a PDF, DOCX, or ODT file."))
            try:
                raw = base64.b64decode(template.source_document, validate=True)
            except (binascii.Error, ValueError, TypeError):
                raise ValidationError(_("The uploaded master is not valid base64 data."))
            if len(raw) > self._MAX_SOURCE_SIZE:
                raise ValidationError(_("The uploaded master cannot exceed 10 MB."))
            valid = extension == ".pdf" and raw.startswith(b"%PDF-")
            if extension in (".docx", ".odt"):
                try:
                    with zipfile.ZipFile(BytesIO(raw)) as archive:
                        names = set(archive.namelist())
                        if extension == ".docx":
                            valid = {"[Content_Types].xml", "word/document.xml"} <= names
                        else:
                            valid = (
                                "mimetype" in names
                                and archive.read("mimetype")
                                == b"application/vnd.oasis.opendocument.text"
                            )
                except (zipfile.BadZipFile, KeyError):
                    valid = False
            if not valid:
                raise ValidationError(_("The file content does not match its PDF, DOCX, or ODT extension."))

    @api.depends("contract_ids")
    def _compute_contract_count(self):
        for template in self:
            template.contract_count = len(template.contract_ids)

    def _get_contract_values(self):
        self.ensure_one()
        return {
            "template_name": self.name,
            "template_version": self.version,
            "template_source_hash": hashlib.sha256(
                base64.b64decode(self.source_document)
            ).hexdigest() if self.source_document else False,
            "rental_type": self.rental_type,
            "billing_frequency": self.billing_frequency,
            "invoice_lead_days": self.invoice_lead_days,
            "require_manager_signature": self.require_manager_signature,
            "late_fee_enabled": self.late_fee_enabled,
            "late_fee_calculation": self.late_fee_calculation,
            "late_fee_amount": self.late_fee_amount,
            "late_fee_percentage": self.late_fee_percentage,
            "late_fee_grace_days": self.late_fee_grace_days,
            "late_fee_product_id": self.late_fee_product_id.id,
            "terms": self.terms,
        }

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
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "view_mode": "form",
            "res_id": revision.id,
        }

    def action_view_contracts(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "apartment_rental_management.action_rental_contract"
        )
        action["domain"] = [("template_id", "=", self.id)]
        action["context"] = {"default_template_id": self.id}
        return action

    def _check_can_apply(self, contract):
        self.ensure_one()
        if self.company_id != contract.company_id:
            raise UserError(_("The template and contract must belong to the same company."))
        reference_date = contract.date_start or fields.Date.context_today(contract)
        if self.effective_date and reference_date < self.effective_date:
            raise UserError(_("This template is not effective on the contract start date."))
        if self.expiration_date and reference_date > self.expiration_date:
            raise UserError(_("This template has expired for the contract start date."))

    def write(self, vals):
        if self.contract_ids and self._LEGAL_FIELDS.intersection(vals):
            raise UserError(_("A template already used by a contract is immutable. Create a new revision instead."))
        return super().write(vals)

    def unlink(self):
        if self.contract_ids:
            raise UserError(_("A template used by a contract cannot be deleted. Archive it instead."))
        return super().unlink()
