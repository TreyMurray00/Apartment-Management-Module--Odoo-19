# Apartment Rental Management

An original Odoo 19 Community addon for managing furnished or unfurnished short-term and
long-term rentals. It uses only Community dependencies and does not require proprietary
Enterprise Rental or Sign modules.

## Main features

- Properties and individual rentable units
- Prospective tenant applications with secure document collection before contract creation
- Required employment/job letter, certificate of character, primary ID, and distinct secondary ID
- Tokenized applicant portal uploads with PDF/JPG/PNG content validation, inline browser preview,
  separate download controls, and a 10 MB per-file limit
- Administrator verification/rejection, reviewer identity and timestamp, expiry checks, duplicate-file
  detection, approval gating, and conversion of approved applications into draft contracts
- Versioned reusable contract templates with effective/expiration dates
- Optional PDF, DOCX, or ODT master-document uploads for legal reference
- Per-contract legal-text snapshots that remain unchanged when a template is revised
- Daily, weekly, monthly, quarterly, and yearly rental periods
- Short-term and long-term rates, deposits, amenities, and occupancy status
- Date-overlap protection for reserved units
- Transaction-level unit reservation locking to prevent concurrent double booking
- Prorated final billing periods
- Contract workflow: Draft → Awaiting Signature → Signed → Active → Ended
- Tenant portal review, PDF download, signature drawing, and communication history
- Optional manager countersignature
- Signatures cryptographically bound to a canonical snapshot of the parties, premises, dates,
  pricing, legal terms, signature policy, and billing schedule
- Final countersigned PDF archived once, hashed, and served unchanged from the tenant portal
- Confirmed contract immutability, controlled amendments, protected audit fields, and token
  rotation when a cancelled contract is reset
- Draft customer invoices generated from due schedule lines
- Separate, duplicate-protected security-deposit invoice
- Separate tenant-charge ledger for electricity, water, gas, internet, waste/sewer, other
  utilities, damage, cleaning, key replacement, and miscellaneous liabilities
- Fixed, quantity-based, and meter-reading calculations with independent service/invoice dates,
  service products, rates, notes, invoices, and chatter attachments
- Contract/template late-fee policy with fixed or percentage fees, grace periods, overdue-invoice
  traceability, and duplicate-safe daily generation
- Versioned late-payment and eviction-notice templates with controlled placeholders, effective
  dates, response/cure periods, and delivery-method defaults
- Notice eligibility based on posted unpaid rent invoices after a separate grace period, including
  consecutive missed-payment thresholds that reset when a paid rent invoice interrupts the streak
- Contract-clause violation records with incident details, clause references, evidence chatter,
  severity, cure, dismissal, and escalation states
- Reviewed notice snapshots, immutable issued PDFs and hashes, email or manual delivery evidence,
  reviewer/issuer/delivery audit fields, and automatic preparation of reviewable late-letter drafts
- Daily scheduled job for due invoices and expired contracts
- Multi-company record rules and Rental User / Administrator roles
- Least-privilege accounting: Rental Users manage leases; Rental Administrators create and view
  invoices
- Template master validation by file content with a 10 MB upload limit

## Setup

1. Install **Apartment Rental Management** from Apps.
2. Grant users a Rental Management role in Settings → Users.
3. Create service products for rent and deposits and configure their income accounts/taxes.
4. Create a prospective tenant record under **Rentals → Operations → Tenant Applications**,
   select the requested unit and dates, and click **Request Documents**. The applicant receives a
   secure link for the four required uploads.
5. A Rental Administrator reviews each uploaded file, records any rejection reason, and verifies
   acceptable documents. Approve the application only after all four documents are verified, then
   create the linked draft rental contract.
6. Create reusable templates under Rentals → Configuration → Contract Templates. Enter the
   mergeable wording in **Contract Body** and optionally upload the original PDF/DOCX/ODT master.
7. Create a property and its units, including short/long rates and the accounting products.
8. Create or review the generated contract, select a template, review the copied legal-text snapshot, and confirm it.
9. Send the signature request. After the tenant signs, record the manager signature if required,
   activate the contract, and create due invoices.
10. For separately billable items, open **Rentals → Operations → Tenant Charges** (or the
    contract's **Utilities and Other Charges** tab), select the charge category and accounting
    product, then enter a fixed amount, quantity, or previous/current meter readings.
11. To automate late fees, enable the policy on a template or draft contract, select the late-fee
    product, choose a fixed amount or percentage of the outstanding balance, and set the grace
    period. The daily rental job creates and invoices one traceable late fee per overdue invoice.
12. Create jurisdiction-reviewed wording under **Rentals → Configuration → Notice Templates**.
    Use separate late-payment and eviction templates, set the response/cure period, and insert only
    the documented placeholders shown on the template form.
13. Configure the payment-notice grace period, consecutive missed-payment threshold, and notice
    templates on a draft contract or contract template. Optional automation prepares late-letter
    drafts for review; it never issues notices automatically.
14. Record alleged clause breaches under **Rentals → Operations → Contract Violations**, including
    the exact signed clause, objective incident facts, and supporting attachments. Prepare notices
    from the active contract, review and freeze them, then issue and record delivery evidence.

## Legal note

The signing flow records evidence and preserves the resulting PDF, but electronic-signature,
deposit, cancellation, tax, registration, and tenancy requirements vary by jurisdiction. Have
the default terms and signing process reviewed locally before production use.

The uploaded master document is retained as a controlled legal reference. Odoo merges and signs
the editable **Contract Body**, so that body must contain the complete operative agreement.

Late fees and tenant-paid utilities may be limited by local leases, consumer rules, rent-control
rules, or utility regulations. The contract body should explicitly state which charges are the
tenant's responsibility and how each late fee is calculated before signatures are requested.

Late-payment and eviction rules vary substantially by jurisdiction, including required wording,
service methods, cure periods, protected-tenancy rules, prohibited retaliation or discrimination,
government filings, and court procedures. The module records workflow evidence but does not decide
whether eviction is lawful. Have every template and each eviction notice reviewed locally before
issuance; never treat an Odoo notice as a substitute for required legal process.

Applicant documents contain sensitive personal information. Limit Rental Administrator access,
use HTTPS, define an appropriate retention/deletion policy, and consider malware scanning at the
reverse proxy or storage layer before using public uploads in production.
