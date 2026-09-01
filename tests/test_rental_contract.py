import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestRentalContract(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(user=cls.env.ref("base.user_root"))
        cls.rent_product = cls.env["product.product"].create(
            {"name": "Apartment Rent", "type": "service"}
        )
        cls.deposit_product = cls.env["product.product"].create(
            {"name": "Rental Deposit", "type": "service"}
        )
        cls.utility_product = cls.env["product.product"].create(
            {"name": "Metered Utilities", "type": "service"}
        )
        cls.late_fee_product = cls.env["product.product"].create(
            {"name": "Rental Late Fee", "type": "service"}
        )
        cls.tenant = cls.env["res.partner"].create(
            {"name": "Test Tenant", "email": "tenant@example.com"}
        )
        cls.property = cls.env["rental.property"].create(
            {"name": "Test Residence", "code": "TEST"}
        )
        cls.unit = cls.env["rental.unit"].create(
            {
                "name": "A-01",
                "property_id": cls.property.id,
                "short_term_rate": 100,
                "long_term_rate": 1000,
                "deposit_amount": 500,
                "rent_product_id": cls.rent_product.id,
                "deposit_product_id": cls.deposit_product.id,
            }
        )
        cls.rental_user = new_test_user(
            cls.env, login="rental-user",
            groups="apartment_rental_management.group_rental_user",
            company_id=cls.env.company.id,
        )
        cls.rental_manager = new_test_user(
            cls.env, login="rental-manager",
            groups="apartment_rental_management.group_rental_manager",
            company_id=cls.env.company.id,
        )

    def _create_contract(self, **values):
        defaults = {
            "unit_id": self.unit.id,
            "partner_id": self.tenant.id,
            "date_start": "2026-01-01",
            "date_end": "2026-01-10",
            "rental_type": "short",
            "billing_frequency": "daily",
            "rent_amount": 100,
            "deposit_amount": 500,
            "rent_product_id": self.rent_product.id,
            "deposit_product_id": self.deposit_product.id,
        }
        defaults.update(values)
        return self.env["rental.contract"].create(defaults)

    def test_daily_schedule_generation(self):
        contract = self._create_contract()
        contract.action_confirm()
        self.assertEqual(contract.state, "sent")
        self.assertEqual(len(contract.billing_line_ids), 10)
        self.assertEqual(sum(contract.billing_line_ids.mapped("amount")), 1000)
        self.assertTrue(contract.access_token)

    def test_last_month_is_prorated(self):
        contract = self._create_contract(
            date_end="2026-02-15",
            rental_type="long",
            billing_frequency="monthly",
            rent_amount=1000,
        )
        contract.action_confirm()
        self.assertEqual(len(contract.billing_line_ids), 2)
        self.assertEqual(contract.billing_line_ids[0].amount, 1000)
        expected = contract.currency_id.round(1000 * 15 / 28)
        self.assertEqual(contract.billing_line_ids[1].amount, expected)

    def test_overlapping_reserved_contract_is_rejected(self):
        first = self._create_contract()
        first.action_confirm()
        second = self._create_contract(date_start="2026-01-05", date_end="2026-01-12")
        with self.assertRaises(ValidationError):
            second.action_confirm()

    def test_dual_signature_required_for_activation(self):
        contract = self._create_contract()
        contract.action_confirm()
        contract._record_tenant_signature(
            base64.b64encode(b"tenant-signature"), self.tenant.name
        )
        contract.manager_signature = base64.b64encode(b"manager-signature")
        contract.action_manager_sign()
        contract.action_activate()
        self.assertEqual(contract.state, "active")
        self.assertTrue(contract.manager_signed_on)
        self.assertTrue(contract.manager_signature_hash)
        self.assertTrue(contract.signed_document_hash)
        self.assertTrue(contract.final_document_attachment_id)
        self.assertTrue(contract.final_document_hash)

    def test_rent_and_deposit_invoices_are_linked(self):
        contract = self._create_contract(date_start="2026-08-30", date_end="2026-08-30")
        contract.action_confirm()
        contract._record_tenant_signature(
            base64.b64encode(b"tenant-signature"), self.tenant.name
        )
        rent_invoice = contract._create_invoice_for_schedule(contract.billing_line_ids)
        self.assertEqual(rent_invoice.move_type, "out_invoice")
        self.assertEqual(rent_invoice.rental_contract_id, contract)
        self.assertEqual(rent_invoice.amount_untaxed, 100)
        self.assertEqual(contract.billing_line_ids.state, "invoiced")

        contract.action_create_deposit_invoice()
        self.assertEqual(contract.deposit_invoice_id.rental_contract_id, contract)
        self.assertEqual(contract.deposit_invoice_id.amount_untaxed, 500)

    def test_template_defaults_are_copied_as_a_snapshot(self):
        template = self.env["rental.contract.template"].create(
            {
                "name": "Short Stay Agreement",
                "version": "3",
                "rental_type": "short",
                "billing_frequency": "weekly",
                "invoice_lead_days": 2,
                "require_manager_signature": False,
                "terms": "<p>Original reusable terms.</p>",
            }
        )
        contract = self.env["rental.contract"].create(
            {
                "template_id": template.id,
                "unit_id": self.unit.id,
                "partner_id": self.tenant.id,
                "date_start": "2026-09-01",
                "date_end": "2026-09-07",
                "rent_amount": 700,
                "rent_product_id": self.rent_product.id,
            }
        )
        self.assertEqual(contract.template_version, "3")
        self.assertEqual(contract.rental_type, "short")
        self.assertEqual(contract.billing_frequency, "weekly")
        self.assertEqual(contract.invoice_lead_days, 2)
        self.assertFalse(contract.require_manager_signature)
        self.assertIn("Original reusable terms", contract.terms)

        with self.assertRaises(UserError):
            template.terms = "<p>Changed future terms.</p>"
        self.assertIn("Original reusable terms", contract.terms)

    def test_confirmed_contract_is_immutable_through_rpc(self):
        contract = self._create_contract()
        contract.action_confirm()
        user_contract = contract.with_user(self.rental_user)
        with self.assertRaises(AccessError):
            user_contract.write({"state": "active"})
        with self.assertRaises(UserError):
            user_contract.write({"rent_amount": 1})
        with self.assertRaises(UserError):
            user_contract.billing_line_ids.write({"amount": 1})

    def test_rental_user_can_create_explicit_draft(self):
        contract = self.env["rental.contract"].with_user(self.rental_user).create({
            "state": "draft",
            "unit_id": self.unit.id,
            "partner_id": self.tenant.id,
            "date_start": "2026-10-01",
            "date_end": "2026-10-03",
            "rental_type": "short",
            "billing_frequency": "daily",
            "rent_amount": 100,
            "rent_product_id": self.rent_product.id,
        })
        self.assertEqual(contract.state, "draft")

    def test_reset_rotates_portal_token(self):
        contract = self._create_contract()
        contract.action_confirm()
        old_token = contract.access_token
        contract.action_cancel()
        contract.with_user(self.rental_manager).action_reset_to_draft()
        self.assertEqual(contract.state, "draft")
        self.assertTrue(contract.access_token)
        self.assertNotEqual(contract.access_token, old_token)

    def test_template_change_recalculates_unit_pricing(self):
        template = self.env["rental.contract.template"].create({
            "name": "Long Stay", "rental_type": "long", "billing_frequency": "quarterly",
            "terms": "<p>Long stay terms.</p>",
        })
        contract = self._create_contract(template_id=template.id, rental_type="short", rent_amount=100)
        contract.action_apply_template()
        self.assertEqual(contract.rental_type, "long")
        self.assertEqual(contract.rent_amount, self.unit.long_term_rate)
        self.assertEqual(contract.billing_frequency, "quarterly")

    def test_accounting_privilege_is_manager_only(self):
        self.assertFalse(self.rental_user.has_group("account.group_account_invoice"))
        self.assertTrue(self.rental_manager.has_group("account.group_account_invoice"))
        contract = self._create_contract()
        contract.action_confirm()
        with self.assertRaises(AccessError):
            contract.with_user(self.rental_user).action_create_due_invoices()

    def test_invalid_master_content_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.env["rental.contract.template"].create({
                "name": "Fake PDF", "terms": "<p>Terms</p>",
                "source_document_filename": "fake.pdf",
                "source_document": base64.b64encode(b"not really a PDF"),
            })

    def test_amendment_preserves_historical_contract(self):
        contract = self._create_contract()
        contract.action_confirm()
        contract._record_tenant_signature(base64.b64encode(b"tenant"), self.tenant.name)
        contract.manager_signature = base64.b64encode(b"manager")
        contract.action_manager_sign()
        action = contract.with_user(self.rental_manager).action_create_amendment()
        amendment = self.env["rental.contract"].browse(action["res_id"])
        self.assertEqual(amendment.state, "draft")
        self.assertEqual(amendment.amends_contract_id, contract)
        self.assertFalse(amendment.tenant_signature)
        self.assertTrue(contract.final_document_attachment_id)

    def test_metered_utility_charge_is_separately_invoiced(self):
        contract = self._create_contract(require_manager_signature=False)
        contract.action_confirm()
        contract._record_tenant_signature(base64.b64encode(b"tenant"), self.tenant.name)
        contract.action_activate()
        charge = self.env["rental.charge"].create({
            "name": "Electricity - September",
            "contract_id": contract.id,
            "category": "electricity",
            "calculation_type": "meter",
            "product_id": self.utility_product.id,
            "service_date": "2026-09-30",
            "invoice_date": "2026-09-30",
            "meter_previous": 100,
            "meter_current": 135.5,
            "unit_price": 2,
            "notes": "Main meter E-100",
        })
        self.assertEqual(charge.quantity, 35.5)
        self.assertEqual(charge.amount, 71)
        invoice = contract._create_invoice_for_charge(charge)
        self.assertEqual(invoice.rental_charge_id, charge)
        self.assertEqual(invoice.amount_untaxed, 71)
        self.assertEqual(charge.state, "invoiced")

    def test_blank_tenant_charge_form_computes_without_currency(self):
        charge = self.env["rental.charge"].new({
            "calculation_type": "fixed", "unit_price": 12.5,
        })
        self.assertFalse(charge.currency_id)
        self.assertEqual(charge.amount, 12.5)

    def test_late_fee_generation_is_duplicate_safe(self):
        contract = self._create_contract(
            date_start="2026-08-01", date_end="2026-08-01",
            require_manager_signature=False,
            late_fee_enabled=True,
            late_fee_calculation="fixed",
            late_fee_amount=25,
            late_fee_grace_days=5,
            late_fee_product_id=self.late_fee_product.id,
        )
        contract.action_confirm()
        contract._record_tenant_signature(base64.b64encode(b"tenant"), self.tenant.name)
        contract.action_activate()
        invoice = contract._create_invoice_for_schedule(contract.billing_line_ids)
        invoice.action_post()
        self.assertEqual(contract.state, "active")
        self.assertTrue(contract.late_fee_enabled)
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(invoice.payment_state, "not_paid")
        self.assertTrue(invoice.invoice_date_due)
        self.assertGreater(invoice.amount_residual, 0)
        late_fee_run_date = invoice.invoice_date_due + timedelta(days=10)
        created = contract._generate_late_fee_charges(late_fee_run_date)
        self.assertEqual(len(created), 1)
        contract._generate_late_fee_charges(late_fee_run_date + timedelta(days=1))
        late_fees = contract.charge_ids.filtered(lambda charge: charge.category == "late_fee")
        self.assertEqual(len(late_fees), 1)
        self.assertEqual(late_fees.amount, 25)
        self.assertEqual(late_fees.source_invoice_id, invoice)

    def _create_notice_template(self, notice_type, **values):
        defaults = {
            "name": f"{notice_type.replace('_', ' ').title()} Test Template",
            "version": "1",
            "notice_type": notice_type,
            "default_ground": "nonpayment" if notice_type == "late_payment" else "repeated_nonpayment",
            "cure_days": 5,
            "delivery_method": "hand",
            "subject": "Notice for {{ tenant_name }} - {{ contract_number }}",
            "body_html": (
                "<p>Amount due: {{ amount_due }}</p>"
                "<p>Invoices: {{ invoice_references }}</p>"
                "<p>Grounds: {{ grounds }}</p>"
            ),
        }
        defaults.update(values)
        return self.env["rental.notice.template"].create(defaults)

    def _activate_notice_contract(self, days=1, **values):
        today = fields.Date.context_today(self.env["rental.contract"])
        start_date = today - timedelta(days=365)
        end_date = start_date + timedelta(days=days - 1)
        defaults = {
            "date_start": start_date, "date_end": end_date,
            "require_manager_signature": False, "payment_notice_grace_days": 5,
        }
        defaults.update(values)
        contract = self._create_contract(**defaults)
        contract.action_confirm()
        contract._record_tenant_signature(base64.b64encode(b"tenant"), self.tenant.name)
        contract.action_activate()
        return contract

    def _post_overdue_rent_invoice(self, contract, schedule, days_overdue=30):
        invoice = contract._create_invoice_for_schedule(schedule)
        invoice.invoice_date_due = fields.Date.context_today(invoice) - timedelta(days=days_overdue)
        invoice.action_post()
        return invoice

    def test_late_payment_notice_review_issue_and_delivery(self):
        late_template = self._create_notice_template("late_payment")
        contract = self._activate_notice_contract(late_notice_template_id=late_template.id)
        invoice = self._post_overdue_rent_invoice(contract, contract.billing_line_ids)

        action = contract.action_prepare_late_notice()
        notice = self.env["rental.notice"].browse(action["res_id"])
        self.assertEqual(notice.state, "draft")
        notice.action_approve()
        self.assertEqual(notice.state, "approved")
        self.assertEqual(notice.source_invoice_ids, invoice)
        self.assertEqual(notice.amount_due, invoice.amount_residual)
        self.assertIn(self.tenant.name, notice.subject)
        self.assertNotIn("{{ amount_due }}", notice.body_html)
        self.assertTrue(notice.eligibility_snapshot)

        notice.action_issue()
        self.assertEqual(notice.state, "issued")
        self.assertTrue(notice.document_attachment_id)
        self.assertTrue(notice.document_hash)
        preview_action = notice.action_preview_pdf()
        self.assertEqual(preview_action["type"], "ir.actions.act_url")
        self.assertIn(f"/web/content/{notice.document_attachment_id.id}", preview_action["url"])
        with self.assertRaises(UserError):
            notice.action_mark_delivered()
        with self.assertRaises(ValidationError):
            notice.write({
                "delivery_proof_filename": "proof.pdf",
                "delivery_proof": base64.b64encode(b"not a PDF"),
            })
        notice.write({"delivery_reference": "HAND-001", "delivery_confirmed": True})
        notice.action_mark_delivered()
        self.assertEqual(notice.state, "delivered")
        self.assertTrue(notice.delivered_on)
        with self.assertRaises(UserError):
            notice.with_user(self.rental_manager).write({"delivery_reference": "CHANGED"})
        with self.assertRaises(UserError):
            notice.unlink()

    def test_late_notice_waits_until_grace_period_has_elapsed(self):
        contract = self._activate_notice_contract()
        invoice = contract._create_invoice_for_schedule(contract.billing_line_ids)
        invoice.action_post()
        grace_end = invoice.invoice_date_due + timedelta(days=contract.payment_notice_grace_days)
        self.assertFalse(contract._eligible_overdue_rent_invoices(grace_end))
        self.assertEqual(
            contract._eligible_overdue_rent_invoices(grace_end + timedelta(days=1)), invoice
        )

    def test_repeated_nonpayment_eviction_requires_threshold(self):
        eviction_template = self._create_notice_template("eviction")
        contract = self._activate_notice_contract(
            days=3, eviction_notice_template_id=eviction_template.id,
            eviction_nonpayment_threshold=3,
        )
        for index, schedule in enumerate(contract.billing_line_ids):
            self._post_overdue_rent_invoice(contract, schedule, days_overdue=30 - index)
        action = contract.action_prepare_eviction_notice()
        notice = self.env["rental.notice"].browse(action["res_id"])
        self.assertEqual(notice.ground, "repeated_nonpayment")
        with self.assertRaises(UserError):
            contract.action_prepare_eviction_notice()
        notice.action_approve()
        self.assertEqual(notice.consecutive_missed_payments, 3)
        self.assertEqual(len(notice.source_invoice_ids), 3)

    def test_contract_violation_can_support_eviction_review(self):
        eviction_template = self._create_notice_template(
            "eviction", default_ground="contract_violation",
            body_html="<p>Clause {{ clause_references }}: {{ violation_details }}</p>",
        )
        contract = self._activate_notice_contract(
            eviction_notice_template_id=eviction_template.id,
            eviction_nonpayment_threshold=3,
        )
        self._post_overdue_rent_invoice(contract, contract.billing_line_ids)
        violation = self.env["rental.contract.violation"].create({
            "name": "Unauthorized subletting", "contract_id": contract.id,
            "clause_reference": "Clause 8.2", "incident_date": contract.date_start,
            "description": "A third party occupied the unit without written consent.",
            "severity": "material",
        })
        violation.action_escalate()
        with self.assertRaises(UserError):
            violation.with_user(self.rental_manager).write({"description": "Changed facts"})
        action = contract.action_prepare_eviction_notice()
        notice = self.env["rental.notice"].browse(action["res_id"])
        self.assertEqual(notice.ground, "contract_violation")
        self.assertEqual(notice.violation_ids, violation)
        notice.action_approve()
        self.assertFalse(notice.source_invoice_ids)
        self.assertEqual(notice.amount_due, 0)
        self.assertIn("Clause 8.2", notice.body_html)
        violation.action_reopen()
        with self.assertRaises(UserError):
            violation.with_user(self.rental_manager).write({"description": "Rewritten facts"})
        violation.write({"resolution_notes": "Tenant removed the unauthorized occupant."})
        violation.action_mark_cured()
        with self.assertRaises(UserError):
            notice.action_issue()

    def test_notice_templates_are_immutable_after_use(self):
        late_template = self._create_notice_template("late_payment")
        contract = self._activate_notice_contract(late_notice_template_id=late_template.id)
        self._post_overdue_rent_invoice(contract, contract.billing_line_ids)
        action = contract.action_prepare_late_notice()
        notice = self.env["rental.notice"].browse(action["res_id"])
        with self.assertRaises(UserError):
            contract.action_prepare_late_notice()
        with self.assertRaises(UserError):
            late_template.write({"subject": "Changed after use"})
        notice.action_approve()
        notice.write({"cancellation_reason": "Prepared against the wrong correspondence address."})
        notice.action_cancel()
        self.assertEqual(notice.state, "cancelled")
        self.assertTrue(notice.cancelled_on)
        with self.assertRaises(UserError):
            notice.unlink()

    def test_ended_contract_can_still_receive_late_payment_letter(self):
        late_template = self._create_notice_template("late_payment")
        contract = self._activate_notice_contract(late_notice_template_id=late_template.id)
        self._post_overdue_rent_invoice(contract, contract.billing_line_ids)
        contract.action_end()
        action = contract.action_prepare_late_notice()
        notice = self.env["rental.notice"].browse(action["res_id"])
        self.assertEqual(contract.state, "ended")
        self.assertEqual(notice.notice_type, "late_payment")

    def test_notice_template_rejects_unknown_placeholder(self):
        with self.assertRaises(ValidationError):
            self._create_notice_template(
                "late_payment", subject="Unknown {{ tenant_magic_value }}"
            )
        with self.assertRaises(ValidationError):
            self._create_notice_template(
                "late_payment", subject="Broken {{ tenant_name }"
            )

    def test_rental_user_cannot_review_legal_notice(self):
        late_template = self._create_notice_template("late_payment")
        contract = self._activate_notice_contract(late_notice_template_id=late_template.id)
        self._post_overdue_rent_invoice(contract, contract.billing_line_ids)
        action = contract.action_prepare_late_notice()
        notice = self.env["rental.notice"].browse(action["res_id"])
        with self.assertRaises(AccessError):
            notice.with_user(self.rental_user).action_approve()

    def _create_application_document(self, application, document_type, marker, **values):
        document_values = {
            "application_id": application.id,
            "document_type": document_type,
            "name": document_type.replace("_", " ").title(),
            "filename": f"{document_type}.pdf",
            "file": base64.b64encode(b"%PDF-1.4\n" + marker.encode()),
        }
        document_values.update(values)
        return self.env["rental.application.document"].create(document_values)

    def test_applicant_document_preview_uses_tokenized_inline_route(self):
        application = self.env["rental.application"].create({"partner_id": self.tenant.id})
        document = self._create_application_document(
            application, "employment_letter", "preview-document"
        )
        action = document.action_preview()
        self.assertEqual(action["target"], "new")
        self.assertIn(f"/my/rental-applications/{application.id}/documents/{document.id}", action["url"])
        self.assertIn("access_token=", action["url"])
        self.assertIn("preview=true", action["url"])

    def test_default_rent_product_setup_is_idempotent(self):
        ensured_product = self.env["rental.unit"]._ensure_default_rent_product()
        default_product = self.env.ref(
            "apartment_rental_management.product_rental_rent_default"
        )
        self.assertEqual(ensured_product, default_product)
        self.assertEqual(
            self.env["rental.unit"]._ensure_default_rent_product(), default_product
        )

    def test_application_requires_and_verifies_four_distinct_documents(self):
        application = self.env["rental.application"].create({
            "partner_id": self.tenant.id,
            "unit_id": self.unit.id,
            "rental_type": "short",
            "requested_date_start": "2026-11-01",
            "requested_date_end": "2026-11-07",
        })
        application.sudo().write({"state": "awaiting_documents"})
        documents = self.env["rental.application.document"]
        for index, document_type in enumerate((
            "employment_letter", "character_certificate", "primary_id", "secondary_id"
        )):
            documents |= self._create_application_document(
                application, document_type, f"unique-document-{index}"
            )
        self.assertTrue(application.document_complete)
        self.assertFalse(application.all_documents_verified)
        documents.action_verify()
        self.assertTrue(application.all_documents_verified)
        application.action_start_review()
        application.action_approve()
        action = application.action_create_contract()
        contract = self.env["rental.contract"].browse(action["res_id"])
        self.assertEqual(application.state, "approved")
        self.assertEqual(contract.application_id, application)
        self.assertEqual(contract.partner_id, self.tenant)
        self.assertEqual(contract.billing_frequency, "daily")

    def test_same_file_cannot_satisfy_both_id_requirements(self):
        application = self.env["rental.application"].create({"partner_id": self.tenant.id})
        duplicate = base64.b64encode(b"%PDF-1.4\nthe-same-id")
        self.env["rental.application.document"].create({
            "application_id": application.id, "document_type": "primary_id",
            "name": "Primary ID", "filename": "primary.pdf", "file": duplicate,
        })
        with self.assertRaises(ValidationError):
            self.env["rental.application.document"].create({
                "application_id": application.id, "document_type": "secondary_id",
                "name": "Secondary ID", "filename": "secondary.pdf", "file": duplicate,
            })

    def test_spoofed_applicant_document_is_rejected(self):
        application = self.env["rental.application"].create({"partner_id": self.tenant.id})
        with self.assertRaises(ValidationError):
            self.env["rental.application.document"].create({
                "application_id": application.id,
                "document_type": "employment_letter",
                "name": "Fake Job Letter", "filename": "letter.pdf",
                "file": base64.b64encode(b"not a real PDF"),
            })

    def test_unapproved_application_cannot_be_linked_to_contract(self):
        application = self.env["rental.application"].create({
            "partner_id": self.tenant.id, "unit_id": self.unit.id,
        })
        with self.assertRaises(ValidationError):
            self._create_contract(application_id=application.id)

    def test_approved_application_uses_default_rent_product_for_existing_unit(self):
        legacy_unit = self.env["rental.unit"].create({
            "name": "LEGACY-01", "property_id": self.property.id,
            "short_term_rate": 80, "long_term_rate": 800,
        })
        legacy_unit.rent_product_id = False
        application = self.env["rental.application"].create({
            "partner_id": self.tenant.id, "unit_id": legacy_unit.id,
            "rental_type": "long", "requested_date_start": "2027-01-01",
            "requested_date_end": "2027-01-31",
        })
        application.sudo().write({"state": "approved"})
        action = application.action_create_contract()
        contract = self.env["rental.contract"].browse(action["res_id"])
        default_product = self.env.ref(
            "apartment_rental_management.product_rental_rent_default"
        )
        self.assertEqual(legacy_unit.rent_product_id, default_product)
        self.assertEqual(contract.rent_product_id, default_product)
        self.assertEqual(contract.rent_amount, 800)

    def test_application_contract_requires_configured_rate(self):
        zero_rate_unit = self.env["rental.unit"].create({
            "name": "ZERO-01", "property_id": self.property.id,
            "short_term_rate": 0, "long_term_rate": 0,
        })
        application = self.env["rental.application"].create({
            "partner_id": self.tenant.id, "unit_id": zero_rate_unit.id,
            "rental_type": "long", "requested_date_start": "2027-02-01",
            "requested_date_end": "2027-02-28",
        })
        application.sudo().write({"state": "approved"})
        with self.assertRaises(UserError):
            application.action_create_contract()
