import binascii
import base64

from odoo import _, http
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class RentalCustomerPortal(CustomerPortal):
    def _rental_contract_domain(self):
        partner = request.env.user.partner_id.commercial_partner_id
        return [("partner_id", "child_of", partner.id)]

    def _rental_application_domain(self):
        partner = request.env.user.partner_id.commercial_partner_id
        return [("partner_id", "child_of", partner.id)]

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "rental_contract_count" in counters:
            values["rental_contract_count"] = request.env["rental.contract"].search_count(
                self._rental_contract_domain()
            )
        if "rental_application_count" in counters:
            values["rental_application_count"] = request.env["rental.application"].search_count(
                self._rental_application_domain()
            )
        return values

    @http.route(
        ["/my/rental-applications", "/my/rental-applications/page/<int:page>"],
        type="http", auth="user", website=True,
    )
    def portal_my_rental_applications(self, page=1, **kwargs):
        Application = request.env["rental.application"]
        domain = self._rental_application_domain()
        pager = portal_pager(
            url="/my/rental-applications", total=Application.search_count(domain),
            page=page, step=self._items_per_page,
        )
        applications = Application.search(
            domain, order="create_date desc", limit=self._items_per_page, offset=pager["offset"]
        )
        request.session["my_rental_application_history"] = applications.ids[:100]
        return request.render("apartment_rental_management.portal_my_rental_applications", {
            "applications": applications.sudo(), "page_name": "rental_applications",
            "pager": pager, "default_url": "/my/rental-applications",
        })

    @http.route(
        ["/my/rental-applications/<int:application_id>"],
        type="http", auth="public", website=True,
    )
    def portal_rental_application(self, application_id, access_token=None, message=None, **kwargs):
        try:
            application = self._document_check_access(
                "rental.application", application_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return request.redirect("/my")
        values = self._get_page_view_values(
            application, access_token,
            {
                "application": application.sudo(), "page_name": "rental_application",
                "message": message, "res_company": application.company_id,
            },
            "my_rental_application_history", False, **kwargs,
        )
        return request.render("apartment_rental_management.portal_rental_application_page", values)

    @http.route(
        ["/my/rental-applications/<int:application_id>/upload"],
        type="http", auth="public", website=True, methods=["POST"], csrf=True,
    )
    def portal_rental_application_upload(self, application_id, access_token=None, **post):
        try:
            application = self._document_check_access(
                "rental.application", application_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return request.redirect("/my")
        base_url = application.get_portal_url()
        if application.state not in ("draft", "awaiting_documents", "submitted", "under_review"):
            return request.redirect(f"{base_url}&message=closed")
        document_type = post.get("document_type")
        allowed_types = {"employment_letter", "character_certificate", "primary_id", "secondary_id"}
        upload = request.httprequest.files.get("document")
        if document_type not in allowed_types or not upload or not upload.filename:
            return request.redirect(f"{base_url}&message=missing_file")
        raw = upload.read(10 * 1024 * 1024 + 1)
        if len(raw) > 10 * 1024 * 1024:
            return request.redirect(f"{base_url}&message=file_too_large")
        existing = application.sudo().document_ids.filtered(
            lambda document: document.document_type == document_type
        )
        if existing.filtered(lambda document: document.state in ("pending", "verified")):
            return request.redirect(f"{base_url}&message=already_submitted")
        try:
            existing.filtered(lambda document: document.state == "rejected").sudo().unlink()
            labels = dict(request.env["rental.application.document"]._fields["document_type"].selection)
            request.env["rental.application.document"].sudo().create({
                "application_id": application.id, "document_type": document_type,
                "name": labels[document_type], "filename": upload.filename,
                "file": base64.b64encode(raw),
                "issue_date": post.get("issue_date") or False,
                "expiration_date": post.get("expiration_date") or False,
            })
            application.invalidate_recordset(["document_ids", "document_complete"])
            application.sudo().write({
                "state": "submitted" if application.document_complete else "awaiting_documents"
            })
            application.message_post(
                body=_("Applicant uploaded %s for review.", labels[document_type]),
                author_id=application.partner_id.id,
            )
        except (ValidationError, UserError, ValueError):
            return request.redirect(f"{base_url}&message=invalid_file")
        return request.redirect(f"{base_url}&message=upload_ok")

    @http.route(
        ["/my/rental-applications/<int:application_id>/documents/<int:document_id>"],
        type="http", auth="public", website=True,
    )
    def portal_rental_application_document(
        self, application_id, document_id, access_token=None, download=False, **kwargs
    ):
        try:
            application = self._document_check_access(
                "rental.application", application_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return request.redirect("/my")
        document = application.sudo().document_ids.filtered(lambda item: item.id == document_id)
        if not document:
            return request.not_found()
        stream = request.env["ir.binary"]._get_stream_from(
            document, field_name="file", filename=document.filename
        )
        as_attachment = str(download).lower() in ("1", "true", "yes")
        return stream.get_response(as_attachment=as_attachment)

    @http.route(
        ["/my/rental-contracts", "/my/rental-contracts/page/<int:page>"],
        type="http",
        auth="user",
        website=True,
    )
    def portal_my_rental_contracts(self, page=1, **kwargs):
        Contract = request.env["rental.contract"]
        domain = self._rental_contract_domain()
        pager = portal_pager(
            url="/my/rental-contracts",
            total=Contract.search_count(domain),
            page=page,
            step=self._items_per_page,
        )
        contracts = Contract.search(
            domain,
            order="date_start desc",
            limit=self._items_per_page,
            offset=pager["offset"],
        )
        request.session["my_rental_contract_history"] = contracts.ids[:100]
        return request.render(
            "apartment_rental_management.portal_my_rental_contracts",
            {
                "contracts": contracts.sudo(),
                "page_name": "rental_contracts",
                "pager": pager,
                "default_url": "/my/rental-contracts",
            },
        )

    @http.route(
        ["/my/rental-contracts/<int:contract_id>"],
        type="http",
        auth="public",
        website=True,
    )
    def portal_rental_contract(
        self, contract_id, access_token=None, report_type=None, download=False, message=None, **kwargs
    ):
        try:
            contract = self._document_check_access(
                "rental.contract", contract_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return request.redirect("/my")
        if report_type in ("html", "pdf", "text"):
            if report_type == "pdf" and contract.final_document_attachment_id:
                stream = request.env["ir.binary"]._get_stream_from(
                    contract.final_document_attachment_id.sudo()
                )
                return stream.get_response(as_attachment=download)
            return self._show_report(
                model=contract,
                report_type=report_type,
                report_ref="apartment_rental_management.action_report_rental_contract",
                download=download,
            )
        values = self._get_page_view_values(
            contract,
            access_token,
            {
                "contract": contract,
                "page_name": "rental_contract",
                "message": message,
                "report_type": "html",
                "res_company": contract.company_id,
            },
            "my_rental_contract_history",
            False,
            **kwargs,
        )
        return request.render(
            "apartment_rental_management.portal_rental_contract_page", values
        )

    @http.route(
        ["/my/rental-contracts/<int:contract_id>/accept"],
        type="jsonrpc",
        auth="public",
        website=True,
    )
    def portal_rental_contract_accept(
        self, contract_id, access_token=None, name=None, signature=None
    ):
        access_token = access_token or request.httprequest.args.get("access_token")
        try:
            contract = self._document_check_access(
                "rental.contract", contract_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return {"error": _("Invalid rental contract.")}
        if contract.state != "sent" or contract.tenant_signature:
            return {"error": _("This contract is not awaiting a tenant signature.")}
        if not signature or not name:
            return {"error": _("Signer name and signature are required.")}
        try:
            author = (
                contract.partner_id if request.env.user._is_public()
                else request.env.user.partner_id
            )
            contract._record_tenant_signature(
                signature, name, request.httprequest.remote_addr,
                request.httprequest.user_agent.string, author.id,
            )
            request.env.cr.flush()
        except (TypeError, binascii.Error, ValueError, UserError) as error:
            if isinstance(error, UserError):
                return {"error": error.args[0]}
            return {"error": _("Invalid signature data.")}
        return {
            "force_refresh": True,
            "redirect_url": contract.get_portal_url(query_string="&message=sign_ok"),
        }
