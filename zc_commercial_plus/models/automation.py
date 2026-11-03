from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

COST_TYPES = [("material", "Material"), ("labour", "Installation / Labour"),
              ("accessory", "Accessories"), ("other", "Taxes, insurance & other")]


# ---------------------------------------------------------------------------
# Price analysis kept on the BOQ item: a new quotation line starts from it
# ---------------------------------------------------------------------------
class ZcBoqCostTemplate(models.Model):
    _name = "zc.boq.cost.template"
    _description = "Standard Price Analysis of a BOQ Item"
    _order = "product_tmpl_id, sequence, id"

    sequence = fields.Integer(default=10)
    product_tmpl_id = fields.Many2one("product.template", string="BOQ item", required=True, ondelete="cascade",
                                      index=True)
    cost_type = fields.Selection(COST_TYPES, string="Type", default="material", required=True)
    product_id = fields.Many2one("product.product", string="Material / Service")
    name = fields.Char(string="Description")
    qty_per_unit = fields.Float(string="Qty per BOQ Unit", default=1.0, digits="Product Unit")
    unit_cost = fields.Float(string="Budget Unit Cost", digits="Product Price")
    unit_total = fields.Float(string="Cost per BOQ Unit", compute="_compute_unit_total", digits="Product Price")

    @api.depends("qty_per_unit", "unit_cost")
    def _compute_unit_total(self):
        for row in self:
            row.unit_total = row.qty_per_unit * row.unit_cost

    def _cost_line_vals(self, sale_line):
        return [{"sale_line_id": sale_line.id, "order_id": sale_line.order_id.id, "sequence": t.sequence,
                 "cost_type": t.cost_type, "product_id": t.product_id.id, "name": t.name,
                 "qty_per_unit": t.qty_per_unit, "unit_cost": t.unit_cost} for t in self]


class ProductTemplate(models.Model):
    _inherit = "product.template"

    zc_cost_template_ids = fields.One2many("zc.boq.cost.template", "product_tmpl_id", string="Price analysis")
    zc_template_unit_cost = fields.Float(string="Standard cost per unit", compute="_compute_zc_template_unit_cost",
                                         digits="Product Price")

    @api.depends("zc_cost_template_ids.unit_total")
    def _compute_zc_template_unit_cost(self):
        for tmpl in self:
            tmpl.zc_template_unit_cost = sum(tmpl.zc_cost_template_ids.mapped("unit_total"))


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._zc_load_cost_template()
        return lines

    def _zc_load_cost_template(self):
        """A BOQ item added to a contracting quotation brings its standard price analysis (material x waste,
        installation...), so the item budget is there without typing it line by line."""
        vals = []
        for line in self:
            if line.display_type or line.is_downpayment or line.zc_cost_line_ids \
                    or line.order_id.hom_project_type != "contracting":
                continue
            vals += line.product_id.product_tmpl_id.zc_cost_template_ids._cost_line_vals(line)
        if vals:
            self.env["zc.boq.cost.line"].create(vals)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    zc_markup_pct = fields.Float(string="Markup %", help="Selling price = budget unit cost x (1 + markup).")
    zc_create_site_store = fields.Boolean(string="Create a site store at the handover", default=True,
                                          help="The project gets its own warehouse (receipts, transfers, issues).")

    def action_zc_apply_markup(self):
        for order in self:
            if order.state not in ("draft", "sent"):
                raise UserError(_("Prices are set from the markup on a quotation only."))
            lines = order.order_line.filtered(lambda l: not l.display_type and not l.is_downpayment and l.zc_unit_cost)
            if not lines:
                raise UserError(_("Price the items first (Item Budget tab)."))
            for line in lines:
                line.price_unit = order.currency_id.round(line.zc_unit_cost * (1 + order.zc_markup_pct / 100.0))
            order.message_post(body=_("Selling prices set from the item budget at a %s%% markup.", order.zc_markup_pct))
        return True

    def action_zc_save_cost_templates(self):
        """Keep the price analysis of this contract as the standard one of its BOQ items."""
        Template = self.env["zc.boq.cost.template"]
        done = []
        for order in self:
            for line in order.order_line.filtered("zc_cost_line_ids"):
                tmpl = line.product_id.product_tmpl_id
                tmpl.zc_cost_template_ids.unlink()
                Template.create([{"product_tmpl_id": tmpl.id, "sequence": c.sequence, "cost_type": c.cost_type,
                                  "product_id": c.product_id.id, "name": c.name, "qty_per_unit": c.qty_per_unit,
                                  "unit_cost": c.unit_cost} for c in line.zc_cost_line_ids])
                done.append(tmpl.default_code or tmpl.name)
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"type": "success", "message": _("Standard price analysis saved for: %s", ", ".join(done))
                           if done else _("No priced item to save.")}}

    def _zc_make_site_store(self):
        Warehouse = self.env["stock.warehouse"].sudo()
        for order in self:
            project = order.project_ids[:1]
            if not project or project.zc_warehouse_id or not order.zc_create_site_store:
                continue
            digits = "".join(ch for ch in order.name if ch.isdigit())[-3:] or str(order.id)
            code, n = "P%s" % digits, 0
            while Warehouse.with_context(active_test=False).search_count(
                    [("code", "=", code), ("company_id", "=", order.company_id.id)]):
                n += 1
                code = ("P%s%s" % (digits, n))[-5:]
            project.zc_warehouse_id = Warehouse.create({
                "name": _("Site %s", project.name), "code": code, "company_id": order.company_id.id})
            order.message_post(body=_("Site store %(code)s created for the project.", code=code))

    def action_zc_handover_to_execution(self):
        self._zc_make_site_store()
        return super().action_zc_handover_to_execution()


# ---------------------------------------------------------------------------
# Purchases bought for a contract carry its project without typing it
# ---------------------------------------------------------------------------
class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    def _zc_project(self):
        self.ensure_one()
        if self.zc_boq_line_id:
            project = self.zc_boq_line_id.order_id.project_ids[:1]
            if project:
                return project
        warehouse = self.order_id.picking_type_id.warehouse_id
        if warehouse:
            return self.env["project.project"].sudo().search([("zc_warehouse_id", "=", warehouse.id)], limit=1)
        return self.env["project.project"]

    @api.depends("product_id", "order_id.partner_id", "order_id.picking_type_id", "zc_boq_line_id")
    def _compute_analytic_distribution(self):
        super()._compute_analytic_distribution()
        for line in self:
            if line.display_type or line.analytic_distribution:
                continue
            account = line._zc_project().account_id
            if account:
                line.analytic_distribution = {str(account.id): 100}

    def write(self, vals):
        res = super().write(vals)
        if "analytic_distribution" in vals or "product_id" in vals:
            self.filtered(lambda l: not l.zc_boq_line_id and l.product_id)._zc_guess_boq_line()
        return res


class ResPartner(models.Model):
    _inherit = "res.partner"

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if "country_id" in fields_list and not res.get("country_id") and self.env.company.country_id:
            res["country_id"] = self.env.company.country_id.id
        return res


# ---------------------------------------------------------------------------
# The project shows what the contract produces on site
# ---------------------------------------------------------------------------
class ProjectProject(models.Model):
    _inherit = "project.project"

    zc_daily_report_count = fields.Integer(compute="_compute_zc_site_counts")
    zc_wir_count = fields.Integer(compute="_compute_zc_site_counts")
    zc_material_request_count = fields.Integer(compute="_compute_zc_site_counts")
    zc_claim_count = fields.Integer(compute="_compute_zc_site_counts")
    zc_guarantee_count = fields.Integer(compute="_compute_zc_site_counts")

    _ZC_COUNTS = [("zc_daily_report_count", "zc.daily.report"), ("zc_wir_count", "zc.inspection.request"),
                  ("zc_material_request_count", "zc.material.request"), ("zc_claim_count", "zc.claim"),
                  ("zc_guarantee_count", "hom.bank.guarantee")]

    def _zc_contract(self):
        self.ensure_one()
        return self.sale_order_id or self.reinvoiced_sale_order_id if "reinvoiced_sale_order_id" in self._fields \
            else self.sale_order_id

    def _compute_zc_site_counts(self):
        for project in self:
            order = project._zc_contract()
            for fname, model in self._ZC_COUNTS:
                project[fname] = self.env[model].search_count([("sale_order_id", "=", order.id)]) if order else 0

    def _zc_open(self, model, title):
        self.ensure_one()
        order = self._zc_contract()
        return {"type": "ir.actions.act_window", "name": title, "res_model": model, "view_mode": "list,form",
                "domain": [("sale_order_id", "=", order.id)], "context": {"default_sale_order_id": order.id}}

    def action_zc_daily_reports(self):
        return self._zc_open("zc.daily.report", _("Site Daily Reports"))

    def action_zc_wirs(self):
        return self._zc_open("zc.inspection.request", _("Inspection Requests"))

    def action_zc_material_requests(self):
        return self._zc_open("zc.material.request", _("Material Requests"))

    def action_zc_claims(self):
        return self._zc_open("zc.claim", _("Claims"))

    def action_zc_guarantees(self):
        return self._zc_open("hom.bank.guarantee", _("Letters of Guarantee"))
