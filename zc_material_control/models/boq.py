from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    zc_return_days = fields.Integer(string="Supplier Return Window (days)", default=14,
                                    help="Default number of days to return surplus material to a supplier.")
    zc_return_alert_days = fields.Integer(string="Return Alert (days before)", default=5)
    zc_price_tolerance_pct = fields.Float(string="Price Tolerance %", default=5.0,
                                          help="A purchase price above the item budget by more than this % needs "
                                               "the project manager's approval.")
    zc_stores_user_id = fields.Many2one("res.users", string="Stores Responsible")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    zc_return_days = fields.Integer(related="company_id.zc_return_days", readonly=False)
    zc_return_alert_days = fields.Integer(related="company_id.zc_return_alert_days", readonly=False)
    zc_price_tolerance_pct = fields.Float(related="company_id.zc_price_tolerance_pct", readonly=False)
    zc_stores_user_id = fields.Many2one(related="company_id.zc_stores_user_id", readonly=False)


class ProductCategory(models.Model):
    _inherit = "product.category"

    zc_reusable = fields.Boolean(
        string="Reusable on other projects",
        help="Generic material (wire, pipes, paint...) that can move to another contract. Project-specific models "
             "(light fittings, door handles...) are left unticked.")
    zc_tolerance_pct = fields.Float(string="Allowed Over-supply %",
                                    help="Waste / over-supply allowed above the shop-drawing quantity.")


class ResPartner(models.Model):
    _inherit = "res.partner"

    zc_return_days = fields.Integer(string="Return Window (days)",
                                    help="Days this supplier accepts returns. Empty uses the company default.")


class ProjectProject(models.Model):
    _inherit = "project.project"

    zc_warehouse_id = fields.Many2one("stock.warehouse", string="Site Store", check_company=True,
                                      help="The store of this site: receipts, transfers and the material "
                                           "reconciliation of the project are read from it.")


class SaleOrder(models.Model):
    _inherit = "sale.order"

    hom_certificate_frequency = fields.Selection(
        selection_add=[("interval", "Every N days / كل عدد أيام")], ondelete={"interval": "set default"})
    zc_cert_interval_days = fields.Integer(string="Every (days)", default=20)
    zc_stage_valuation = fields.Boolean(
        string="Stage Valuation", help="Certify supplied and installed quantities at different percentages of "
                                       "the BOQ rate (e.g. supplied 60%, supplied and installed 75%).")
    zc_supplied_pct = fields.Float(string="Supplied %", default=60.0)
    zc_installed_pct = fields.Float(string="Installed %", default=75.0)
    zc_cost_line_ids = fields.One2many("zc.boq.cost.line", "order_id", string="Item Budget")
    zc_budget_total = fields.Monetary(string="Item Budget Total", compute="_compute_zc_budget")
    zc_budget_margin = fields.Monetary(string="Budgeted Margin", compute="_compute_zc_budget")

    @api.depends("zc_cost_line_ids.budget_amount", "amount_untaxed")
    def _compute_zc_budget(self):
        for order in self:
            order.zc_budget_total = sum(order.zc_cost_line_ids.mapped("budget_amount"))
            order.zc_budget_margin = order.amount_untaxed - order.zc_budget_total if order.zc_cost_line_ids else 0.0

    @api.constrains("zc_supplied_pct", "zc_installed_pct")
    def _check_zc_stage_pct(self):
        for order in self:
            if not (0 <= order.zc_supplied_pct <= order.zc_installed_pct <= 100):
                raise ValidationError(_("Stage percentages must satisfy 0 ≤ supplied ≤ installed ≤ 100."))


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    zc_sd_qty = fields.Float(string="Shop Drawing Qty", digits="Product Unit", copy=False,
                             help="Quantity of the approved shop drawing (code A/B). Purchasing is checked against "
                                  "it; without it, the contract quantity is used.")
    zc_sd_submittal_id = fields.Many2one("hom.submittal", string="Shop Drawing", copy=False, readonly=True)
    zc_basis_qty = fields.Float(string="Execution Qty", compute="_compute_zc_basis_qty", digits="Product Unit")
    zc_supplied_pct = fields.Float(string="Supplied % (item)", help="Leave 0 to use the contract percentage.")
    zc_installed_pct = fields.Float(string="Installed % (item)", help="Leave 0 to use the contract percentage.")
    zc_cost_line_ids = fields.One2many("zc.boq.cost.line", "sale_line_id", string="Cost Breakdown")
    zc_unit_cost = fields.Float(string="Budget Unit Cost", compute="_compute_zc_unit_cost", digits="Product Price")

    @api.depends("zc_sd_qty", "product_uom_qty")
    def _compute_zc_basis_qty(self):
        for line in self:
            line.zc_basis_qty = line.zc_sd_qty or line.product_uom_qty

    @api.depends("zc_cost_line_ids.unit_cost", "zc_cost_line_ids.qty_per_unit")
    def _compute_zc_unit_cost(self):
        for line in self:
            line.zc_unit_cost = sum(c.unit_cost * c.qty_per_unit for c in line.zc_cost_line_ids)

    def _zc_stage_pcts(self):
        self.ensure_one()
        order = self.order_id
        return (self.zc_supplied_pct or order.zc_supplied_pct, self.zc_installed_pct or order.zc_installed_pct)

    def _zc_measured(self, date=None, exclude_cancelled=True):
        """(installed, supplied) cumulative physical quantities measured on site up to ``date``."""
        self.ensure_one()
        domain = [("sale_line_id", "=", self.id)]
        if date:
            domain.append(("date", "<=", date))
        measurements = self.env["hom.site.measurement"].sudo().search(domain)
        if exclude_cancelled:
            measurements = measurements.filtered(lambda m: m.certificate_id.state != "cancel")
        installed = sum(measurements.filtered(lambda m: m.zc_stage == "installed").mapped("quantity"))
        supplied = sum(measurements.filtered(lambda m: m.zc_stage == "supplied").mapped("quantity"))
        return installed, supplied

    def _zc_valued_qty(self, installed, supplied):
        """Equivalent quantity at the full BOQ rate: installed x installed% + supplied-only x supplied%."""
        self.ensure_one()
        p_sup, p_inst = self._zc_stage_pcts()
        supplied_only = max(supplied - installed, 0.0)
        return (installed * p_inst + supplied_only * p_sup) / 100.0


class ZcBoqCostLine(models.Model):
    _name = "zc.boq.cost.line"
    _description = "BOQ Item Cost Breakdown"
    _order = "order_id, sale_line_id, sequence, id"

    sequence = fields.Integer(default=10)
    sale_line_id = fields.Many2one("sale.order.line", string="BOQ Item", required=True, ondelete="cascade", index=True,
                                   domain="[('order_id', '=', order_id), ('display_type', '=', False), "
                                          "('is_downpayment', '=', False)]")
    order_id = fields.Many2one("sale.order", string="Contract", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="order_id.company_id", store=True)
    currency_id = fields.Many2one(related="order_id.currency_id")
    cost_type = fields.Selection([("material", "Material"), ("labour", "Installation / Labour"),
                                  ("accessory", "Accessories"), ("other", "Taxes, insurance & other")],
                                 string="Type", default="material", required=True)
    product_id = fields.Many2one("product.product", string="Material / Service")
    name = fields.Char(string="Description")
    qty_per_unit = fields.Float(string="Qty per BOQ Unit", default=1.0, digits="Product Unit",
                                help="e.g. 0.17 litre of paint per m².")
    unit_cost = fields.Float(string="Budget Unit Cost", digits="Product Price")
    tolerance_pct = fields.Float(string="Allowed Over-supply %", compute="_compute_tolerance", store=True,
                                 readonly=False)
    budget_qty = fields.Float(string="Required Qty", compute="_compute_quantities", digits="Product Unit")
    allowed_qty = fields.Float(string="Allowed Qty", compute="_compute_quantities", digits="Product Unit")
    ordered_qty = fields.Float(string="Ordered", compute="_compute_quantities", digits="Product Unit")
    received_qty = fields.Float(string="Received", compute="_compute_quantities", digits="Product Unit")
    budget_amount = fields.Monetary(string="Budget", compute="_compute_quantities")
    committed_amount = fields.Monetary(string="Committed", compute="_compute_quantities")

    @api.onchange("sale_line_id")
    def _onchange_sale_line(self):
        if self.sale_line_id:
            self.order_id = self.sale_line_id.order_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("sale_line_id") and not vals.get("order_id"):
                vals["order_id"] = self.env["sale.order.line"].browse(vals["sale_line_id"]).order_id.id
        return super().create(vals_list)

    @api.depends("product_id.categ_id.zc_tolerance_pct")
    def _compute_tolerance(self):
        for line in self:
            line.tolerance_pct = line.product_id.categ_id.zc_tolerance_pct

    def _purchase_lines(self):
        self.ensure_one()
        if not self.product_id:
            return self.env["purchase.order.line"]
        return self.env["purchase.order.line"].sudo().search([
            ("zc_boq_line_id", "=", self.sale_line_id.id), ("product_id", "=", self.product_id.id),
            ("order_id.state", "in", ("purchase", "done"))])

    @api.depends("sale_line_id.zc_basis_qty", "qty_per_unit", "unit_cost", "tolerance_pct")
    def _compute_quantities(self):
        for line in self:
            basis = line.sale_line_id.zc_basis_qty
            line.budget_qty = basis * line.qty_per_unit
            line.allowed_qty = line.budget_qty * (1 + line.tolerance_pct / 100.0)
            line.budget_amount = line.budget_qty * line.unit_cost
            pol = line._purchase_lines() if line.id else self.env["purchase.order.line"]
            line.ordered_qty = sum(pol.mapped("product_qty"))
            line.received_qty = sum(pol.mapped("qty_received"))
            line.committed_amount = sum(pol.mapped("price_subtotal"))
