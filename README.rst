Apartment Rental Management
===========================

An original Odoo 19 Community addon for furnished or unfurnished short-term and long-term rentals. It uses only Community dependencies and does not require proprietary Enterprise Rental or Sign modules.

Main features
-------------

* Properties, individual rentable units, short/long rates, amenities, deposits, and occupancy status.
* Prospective tenant applications with secure job-letter, character-certificate, and two-ID collection.
* Tokenized PDF/JPG/PNG uploads, inline preview, content validation, duplicate detection, approval gating, and a 10 MB per-file limit.
* Versioned contract templates, optional PDF/DOCX/ODT legal masters, immutable legal-text snapshots, and controlled amendments.
* Daily, weekly, monthly, quarterly, and yearly schedules with proration and reservation locking.
* Tenant and manager signatures bound to canonical contract snapshots, with immutable final PDFs and audit hashes.
* Rent, deposit, utilities, damage, cleaning, replacements, miscellaneous liabilities, and duplicate-safe late-fee invoicing.
* Versioned late-payment and eviction-notice templates with controlled placeholders, validity dates, cure periods, and delivery defaults.
* Notice eligibility based on posted unpaid rent invoices after a separate grace period.
* Consecutive missed-payment thresholds that reset when a paid rent invoice interrupts the current streak.
* Contract-clause violation records with incident facts, exact clause references, evidence chatter, severity, cure, dismissal, and escalation.
* Reviewed notice snapshots, immutable issued PDFs, cancellation retention, delivery evidence, and reviewer/issuer/delivery audit fields.
* Optional automatic preparation of late-letter drafts. Automation never reviews, issues, or confirms delivery.
* Multi-company rules and least-privilege Rental User and Rental Administrator roles.

Notice template placeholders
----------------------------

Notice templates accept only the placeholders documented on the form, including ``{{ tenant_name }}``, ``{{ tenant_address }}``, ``{{ contract_number }}``, ``{{ unit }}``, ``{{ property }}``, ``{{ company_name }}``, ``{{ notice_date }}``, ``{{ response_deadline }}``, ``{{ amount_due }}``, ``{{ invoice_references }}``, ``{{ consecutive_missed_payments }}``, ``{{ grounds }}``, ``{{ clause_references }}``, and ``{{ violation_details }}``.

Setup
-----

#. Install **Apartment Rental Management** from Apps and grant the appropriate Rental Management role in Settings.
#. Create rent, deposit, utility, late-fee, and other required service products with the correct accounting configuration.
#. Create and review prospective tenant applications under **Rentals > Operations > Tenant Applications**.
#. Create reusable contract templates under **Rentals > Configuration > Contract Templates**.
#. Create properties and units, then review, confirm, sign, and activate rental contracts.
#. Create jurisdiction-reviewed wording under **Rentals > Configuration > Notice Templates**.
#. Configure notice grace days, consecutive nonpayment thresholds, and notice templates on draft contracts or contract templates.
#. Record alleged breaches under **Rentals > Operations > Contract Violations**, identifying the exact signed clause and objective incident facts.
#. Prepare notices from active contracts, review and freeze their content, issue them, and retain legally appropriate delivery evidence.

Legal and security note
-----------------------

Electronic signatures, deposits, late fees, utilities, cancellation, tax, registration, tenancy, notice, service, and eviction requirements vary by jurisdiction. Eviction may require prescribed wording, specific service methods, protected-tenancy checks, government filings, and court proceedings. This addon records workflow evidence; it does not determine whether eviction is lawful or replace required legal process. Obtain local review of every legal template and each eviction notice before issuance.

Applicant documents and delivery evidence may contain sensitive personal information. Use HTTPS, restrict administrator access, define retention and deletion policies, and use malware scanning appropriate to the deployment.
