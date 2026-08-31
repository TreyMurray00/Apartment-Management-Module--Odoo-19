from odoo import _, models
from odoo.exceptions import UserError


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def unlink(self):
        if not self.env.su:
            protected = self.env["rental.contract"].sudo().search_count(
                [("final_document_attachment_id", "in", self.ids)], limit=1
            )
            historical_final = any(
                attachment.res_model == "rental.contract"
                and (attachment.name or "").endswith(" - Final Signed.pdf")
                for attachment in self
            )
            applicant_document_ids = [
                attachment.res_id for attachment in self
                if attachment.res_model == "rental.application.document" and attachment.res_id
            ]
            protected_applicant_document = self.env["rental.application.document"].sudo().search_count([
                ("id", "in", applicant_document_ids),
                "|", ("state", "=", "verified"), ("application_id.state", "=", "approved"),
            ], limit=1) if applicant_document_ids else False
            if protected or historical_final:
                raise UserError(_("A final signed contract attachment cannot be deleted."))
            if protected_applicant_document:
                raise UserError(_("A verified prospective-tenant document cannot be deleted."))
        return super().unlink()
