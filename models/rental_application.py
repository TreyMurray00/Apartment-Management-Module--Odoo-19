import base64
import binascii
import hashlib
from pathlib import PurePath

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


REQUIRED_DOCUMENT_TYPES = (
    "employment_letter",
    "character_certificate",
    "primary_id",
    "secondary_id",
)


class RentalApplication(models.Model):
    _name = "rental.application"
    _description = "Prospective Tenant Application"
    _inherit = ["portal.mixin", "mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False, index=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("awaiting_documents", "Awaiting Documents"),
            ("submitted", "Documents Submitted"),
            ("under_review", "Under Review"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="draft", required=True, tracking=True, index=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    partner_id = fields.Many2one(
        "res.partner", string="Applicant", required=True, tracking=True, index=True
    )
    unit_id = fields.Many2one("rental.unit", string="Requested Unit", check_company=True, tracking=True)
    rental_type = fields.Selection(
        [("short", "Short Term"), ("long", "Long Term")], default="long", required=True
    )
    requested_date_start = fields.Date(string="Requested Start Date", tracking=True)
    requested_date_end = fields.Date(string="Requested End Date", tracking=True)
    notes = fields.Text()
    rejection_reason = fields.Text(copy=False, tracking=True)
    document_ids = fields.One2many(
        "rental.application.document", "application_id", string="Submitted Documents", copy=False
    )
    document_complete = fields.Boolean(compute="_compute_document_status", store=True)
    all_documents_verified = fields.Boolean(compute="_compute_document_status", store=True)
    missing_document_labels = fields.Char(compute="_compute_document_status", store=True)
    contract_ids = fields.One2many("rental.contract", "application_id", string="Contracts")
    contract_count = fields.Integer(compute="_compute_contract_count")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("rental.application") or _("New")
            if not self.env.su and vals.get("state", "draft") != "draft":
                raise AccessError(_("Applications must be created in draft and advanced with workflow actions."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su and "state" in vals:
            raise AccessError(_("Application status can only be changed by workflow actions."))
        if not self.env.su and any(application.state in ("approved", "rejected", "cancelled") for application in self):
            allowed = {"message_follower_ids", "activity_ids", "activity_state", "activity_user_id", "activity_type_id", "activity_date_deadline"}
            if set(vals).difference(allowed):
                raise UserError(_("A closed tenant application is immutable."))
        return super().write(vals)

    def unlink(self):
        if any(application.document_ids or application.contract_ids for application in self):
            raise UserError(_("An application with submitted documents or a linked contract cannot be deleted. Archive or reject it according to your retention policy."))
        return super().unlink()

    @api.constrains("requested_date_start", "requested_date_end")
    def _check_requested_dates(self):
        for application in self:
            if application.requested_date_start and application.requested_date_end and application.requested_date_end < application.requested_date_start:
                raise ValidationError(_("The requested end date cannot precede the start date."))

    @api.depends("document_ids.document_type", "document_ids.state")
    def _compute_document_status(self):
        labels = dict(self.env["rental.application.document"]._fields["document_type"].selection)
        for application in self:
            present = set(application.document_ids.filtered(lambda doc: doc.state != "rejected").mapped("document_type"))
            verified = set(application.document_ids.filtered(lambda doc: doc.state == "verified").mapped("document_type"))
            missing = [document_type for document_type in REQUIRED_DOCUMENT_TYPES if document_type not in present]
            application.document_complete = not missing
            application.all_documents_verified = all(document_type in verified for document_type in REQUIRED_DOCUMENT_TYPES)
            application.missing_document_labels = ", ".join(labels[item] for item in missing)

    @api.depends("contract_ids")
    def _compute_contract_count(self):
        for application in self:
            application.contract_count = len(application.contract_ids)

    def _compute_access_url(self):
        super()._compute_access_url()
        for application in self:
            application.access_url = f"/my/rental-applications/{application.id}"

    def action_request_documents(self):
        self.ensure_one()
        if self.state not in ("draft", "awaiting_documents", "submitted"):
            raise UserError(_("Documents cannot be requested in the current application state."))
        if not self.partner_id.email:
            raise UserError(_("Set an email address on the applicant before requesting documents."))
        self.sudo()._portal_ensure_token()
        self.sudo().write({"state": "awaiting_documents"})
        template = self.env.ref(
            "apartment_rental_management.mail_template_rental_application_documents",
            raise_if_not_found=False,
        )
        if template:
            template.send_mail(self.id, force_send=True)
        self.message_post(body=_("Secure document request sent to the applicant."))
        return True

    def action_start_review(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental administrators can review applications."))
        for application in self:
            if not application.document_complete:
                raise UserError(_("Required documents are still missing: %s", application.missing_document_labels))
        self.sudo().write({"state": "under_review"})
        return True

    def action_approve(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental administrators can approve applications."))
        today = fields.Date.context_today(self)
        for application in self:
            if application.state not in ("submitted", "under_review"):
                raise UserError(_("Only submitted applications can be approved."))
            if not application.all_documents_verified:
                raise UserError(_("Every required document must be verified before approval."))
            expired = application.document_ids.filtered(
                lambda doc: doc.state == "verified" and doc.expiration_date and doc.expiration_date < today
            )
            if expired:
                raise UserError(_("Expired documents must be replaced before approval: %s", ", ".join(expired.mapped("name"))))
            application.sudo().write({"state": "approved", "rejection_reason": False})
            application.message_post(body=_("Prospective tenant application approved."))
        return True

    def action_reject(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental administrators can reject applications."))
        for application in self:
            if not application.rejection_reason:
                raise UserError(_("Enter a rejection reason before rejecting the application."))
            application.sudo().write({"state": "rejected"})
        return True

    def action_create_contract(self):
        self.ensure_one()
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental administrators can create contracts from applications."))
        if self.state != "approved":
            raise UserError(_("Approve the application before creating a rental contract."))
        if self.contract_ids:
            contract = self.contract_ids[0]
        else:
            if not self.unit_id or not self.requested_date_start or not self.requested_date_end:
                raise UserError(_("Set the requested unit and rental dates before creating a contract."))
            rate = self.unit_id.short_term_rate if self.rental_type == "short" else self.unit_id.long_term_rate
            if rate <= 0:
                rate_label = _("Daily Rate") if self.rental_type == "short" else _("Monthly Rate")
                raise UserError(_("Set a positive %(rate)s on rental unit %(unit)s before creating the contract.", rate=rate_label, unit=self.unit_id.display_name))
            if not self.unit_id.rent_product_id:
                default_rent_product = self.env["rental.unit"]._ensure_default_rent_product()
                self.unit_id.sudo().write({"rent_product_id": default_rent_product.id})
            contract = self.env["rental.contract"].create({
                "application_id": self.id, "unit_id": self.unit_id.id,
                "partner_id": self.partner_id.id, "rental_type": self.rental_type,
                "billing_frequency": "daily" if self.rental_type == "short" else "monthly",
                "date_start": self.requested_date_start, "date_end": self.requested_date_end,
            })
        return {"type": "ir.actions.act_window", "res_model": "rental.contract", "view_mode": "form", "res_id": contract.id}


class RentalApplicationDocument(models.Model):
    _name = "rental.application.document"
    _description = "Prospective Tenant Document"
    _inherit = ["mail.thread"]
    _order = "document_type, id"

    application_id = fields.Many2one(
        "rental.application", required=True, ondelete="restrict", index=True, check_company=True
    )
    company_id = fields.Many2one(related="application_id.company_id", store=True, index=True)
    partner_id = fields.Many2one(related="application_id.partner_id", store=True, index=True)
    document_type = fields.Selection(
        [
            ("employment_letter", "Employment / Job Letter"),
            ("character_certificate", "Certificate of Character"),
            ("primary_id", "Primary Identification"),
            ("secondary_id", "Secondary Identification"),
            ("other", "Other Supporting Document"),
        ],
        required=True, tracking=True, index=True,
    )
    name = fields.Char(required=True)
    file = fields.Binary(required=True, attachment=True, copy=False)
    filename = fields.Char(required=True)
    checksum = fields.Char(required=True, readonly=True, copy=False, index=True)
    issue_date = fields.Date()
    expiration_date = fields.Date()
    state = fields.Selection(
        [("pending", "Pending Review"), ("verified", "Verified"), ("rejected", "Rejected")],
        default="pending", required=True, tracking=True, index=True,
    )
    verified_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    verified_on = fields.Datetime(readonly=True, copy=False)
    rejection_reason = fields.Text(copy=False)

    _application_document_type_uniq = models.Constraint(
        "unique(application_id, document_type)",
        "Only one current document of each type can be submitted per application.",
    )
    _application_document_checksum_uniq = models.Constraint(
        "unique(application_id, checksum)",
        "The same file cannot be used for two application requirements.",
    )
    _MAX_FILE_SIZE = 10 * 1024 * 1024

    @api.model
    def _validated_file_values(self, encoded, filename):
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise ValidationError(_("The uploaded document is not valid base64 data."))
        if not raw or len(raw) > self._MAX_FILE_SIZE:
            raise ValidationError(_("Documents must be non-empty and no larger than 10 MB."))
        extension = PurePath(filename or "").suffix.lower()
        signatures = {
            ".pdf": raw.startswith(b"%PDF-"),
            ".jpg": raw.startswith(b"\xff\xd8\xff"),
            ".jpeg": raw.startswith(b"\xff\xd8\xff"),
            ".png": raw.startswith(b"\x89PNG\r\n\x1a\n"),
        }
        if extension not in signatures or not signatures[extension]:
            raise ValidationError(_("Upload a genuine PDF, JPG, JPEG, or PNG file."))
        return {"checksum": hashlib.sha256(raw).hexdigest()}

    @api.model_create_multi
    def create(self, vals_list):
        batch_types = set()
        batch_checksums = set()
        for vals in vals_list:
            if not vals.get("file") or not vals.get("filename"):
                raise ValidationError(_("A document file and filename are required."))
            vals.update(self._validated_file_values(vals["file"], vals["filename"]))
            type_key = (vals.get("application_id"), vals.get("document_type"))
            checksum_key = (vals.get("application_id"), vals["checksum"])
            if type_key in batch_types or self.search_count([
                ("application_id", "=", vals.get("application_id")),
                ("document_type", "=", vals.get("document_type")),
            ], limit=1):
                raise ValidationError(_("A document has already been submitted for this requirement."))
            if checksum_key in batch_checksums or self.search_count([
                ("application_id", "=", vals.get("application_id")),
                ("checksum", "=", vals["checksum"]),
            ], limit=1):
                raise ValidationError(_("The same file cannot satisfy two application requirements."))
            batch_types.add(type_key)
            batch_checksums.add(checksum_key)
            if not self.env.su and (
                vals.get("state", "pending") != "pending"
                or vals.get("verified_by_id") or vals.get("verified_on")
            ):
                raise AccessError(_("Document verification fields can only be set by workflow actions."))
        return super().create(vals_list)

    def write(self, vals):
        if "file" in vals or "filename" in vals:
            for document in self:
                if not self.env.su and document.state == "verified":
                    raise UserError(_("A verified document cannot be modified."))
                encoded = vals.get("file", document.file)
                filename = vals.get("filename", document.filename)
                values = dict(vals, **self._validated_file_values(encoded, filename))
                values.update({
                    "state": "pending", "verified_by_id": False,
                    "verified_on": False, "rejection_reason": False,
                })
                super(RentalApplicationDocument, document).write(values)
            return True
        if not self.env.su and {"state", "verified_by_id", "verified_on", "checksum"}.intersection(vals):
            raise AccessError(_("Document verification fields can only be set by workflow actions."))
        if not self.env.su and any(document.state == "verified" for document in self):
            raise UserError(_("A verified document cannot be modified."))
        return super().write(vals)

    @api.constrains("issue_date", "expiration_date")
    def _check_dates(self):
        for document in self:
            if document.issue_date and document.expiration_date and document.expiration_date < document.issue_date:
                raise ValidationError(_("The document expiration date cannot precede its issue date."))

    def action_verify(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental administrators can verify applicant documents."))
        self.sudo().write({
            "state": "verified", "verified_by_id": self.env.user.id,
            "verified_on": fields.Datetime.now(), "rejection_reason": False,
        })
        return True

    def action_preview(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": self.application_id.get_portal_url(
                suffix=f"/documents/{self.id}", query_string="&preview=true"
            ),
            "target": "new",
        }

    def action_reject(self):
        if not self.env.su and not self.env.user.has_group("apartment_rental_management.group_rental_manager"):
            raise AccessError(_("Only rental administrators can reject applicant documents."))
        for document in self:
            if not document.rejection_reason:
                raise UserError(_("Enter a rejection reason for %s.", document.name))
        self.sudo().write({"state": "rejected", "verified_by_id": False, "verified_on": False})
        return True

    def unlink(self):
        if any(document.state == "verified" or document.application_id.state == "approved" for document in self):
            raise UserError(_("Verified documents and documents belonging to approved applications cannot be deleted."))
        return super().unlink()
