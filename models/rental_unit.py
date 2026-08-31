from odoo import api, fields, models


class RentalUnit(models.Model):
    _name = "rental.unit"
    _description = "Rental Unit"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "property_id, name"

    @api.model
    def _default_rent_product(self):
        return self._ensure_default_rent_product()

    @api.model
    def _ensure_default_rent_product(self):
        """Lazily create the fallback product after the full registry is ready."""
        xmlid = "apartment_rental_management.product_rental_rent_default"
        product = self.env.ref(xmlid, raise_if_not_found=False)
        if product:
            return product

        # Serialize the first-use path so two workers cannot create duplicate
        # fallback products before either transaction has committed its XML ID.
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [xmlid])
        xmlid_record = self.env["ir.model.data"].sudo().search([
            ("module", "=", "apartment_rental_management"),
            ("name", "=", "product_rental_rent_default"),
        ], limit=1)
        if xmlid_record and xmlid_record.model == "product.product":
            product = self.env["product.product"].sudo().browse(xmlid_record.res_id).exists()
            if product:
                return product

        Product = self.env["product.product"].sudo()
        values = {
            "name": "Apartment Rent",
            "type": "service",
            "sale_ok": True,
            "purchase_ok": False,
        }
        # website_sale adds this required stored computed field. Explicitly seed it
        # because delegated product.product creation does not always apply its default.
        if "publish_date" in Product._fields:
            values["publish_date"] = fields.Datetime.now()
        product = Product.create(values)
        self.env["ir.model.data"]._update_xmlids([{
            "xml_id": xmlid,
            "record": product,
            "noupdate": True,
        }], update=bool(xmlid_record))
        return product

    name = fields.Char(required=True, tracking=True)
    property_id = fields.Many2one(
        "rental.property", required=True, ondelete="cascade", tracking=True, index=True
    )
    company_id = fields.Many2one(related="property_id.company_id", store=True, index=True)
    active = fields.Boolean(default=True)
    unit_type = fields.Selection(
        [
            ("studio", "Studio"),
            ("apartment", "Apartment"),
            ("house", "House"),
            ("room", "Room"),
            ("other", "Other"),
        ],
        default="apartment",
        required=True,
        tracking=True,
    )
    bedrooms = fields.Integer(default=1)
    bathrooms = fields.Float(default=1)
    floor = fields.Char()
    area = fields.Float(string="Area")
    furnished = fields.Boolean()
    short_term_rate = fields.Monetary(string="Daily Rate", currency_field="currency_id")
    long_term_rate = fields.Monetary(string="Monthly Rate", currency_field="currency_id")
    deposit_amount = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", store=True, readonly=True
    )
    rent_product_id = fields.Many2one(
        "product.product", domain="[('type', '=', 'service')]", check_company=True,
        default=_default_rent_product,
    )
    deposit_product_id = fields.Many2one(
        "product.product", domain="[('type', '=', 'service')]", check_company=True
    )
    amenity_notes = fields.Html()
    contract_ids = fields.One2many("rental.contract", "unit_id")
    current_contract_id = fields.Many2one(
        "rental.contract", compute="_compute_occupancy", string="Current Contract"
    )
    occupancy_state = fields.Selection(
        [("available", "Available"), ("occupied", "Occupied")],
        compute="_compute_occupancy",
        search="_search_occupancy_state",
    )

    _name_property_uniq = models.Constraint(
        "unique(name, property_id)", "The unit name must be unique within a property."
    )

    @api.depends("contract_ids.state", "contract_ids.date_start", "contract_ids.date_end")
    def _compute_occupancy(self):
        today = fields.Date.context_today(self)
        for unit in self:
            current = unit.contract_ids.filtered(
                lambda contract: contract.state == "active"
                and contract.date_start <= today <= contract.date_end
            )[:1]
            unit.current_contract_id = current
            unit.occupancy_state = "occupied" if current else "available"

    @api.model
    def _search_occupancy_state(self, operator, value):
        if operator not in ("=", "!=") or value not in ("available", "occupied"):
            raise NotImplementedError
        today = fields.Date.context_today(self)
        occupied_unit_ids = self.env["rental.contract"].search(
            [
                ("state", "=", "active"),
                ("date_start", "<=", today),
                ("date_end", ">=", today),
            ]
        ).unit_id.ids
        wants_occupied = (operator == "=" and value == "occupied") or (
            operator == "!=" and value == "available"
        )
        return [("id", "in" if wants_occupied else "not in", occupied_unit_ids)]

    def action_view_contracts(self):
        self.ensure_one()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "apartment_rental_management.action_rental_contract"
        )
        action["domain"] = [("unit_id", "=", self.id)]
        action["context"] = {"default_unit_id": self.id}
        return action
