from markupsafe import Markup, escape

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class ZcSubPackage(models.Model):
    """A trade package (حزمة أعمال) put out to several subcontractors, their offers compared line by line with the
    item budget, and one awarded."""
    _name = "zc.sub.package"
    _description = "Subcontract Package"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    title = fields.Char(required=True, tracking=True)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True,
                                    domain="[('state', '=', 'sale'), ('hom_project_type', '=', 'contracting')]")
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id")
    sale_line_ids = fields.Many2many("sale.order.line", string="BOQ items",
                                     domain="[('order_id', '=', sale_order_id), ('display_type', '=', False), ('is_downpayment', '=', False)]")
    product_id = fields.Many2one("product.product", string="Service bought", required=True,
                                 domain="[('type', '=', 'service'), ('purchase_ok', '=', True)]")
    scope = fields.Html(string="Scope & responsibilities",
                        help="Who supplies, stores and tests the materials, wastage, payment conditions...")
    deadline = fields.Date(string="Offers due")
    partner_ids = fields.Many2many("res.partner", string="Invited subcontractors")
    rfq_ids = fields.One2many("purchase.order", "zc_package_id", string="Offers")
    state = fields.Selection([("draft", "Draft"), ("tender", "Offers requested"), ("awarded", "Awarded"),
                              ("cancel", "Cancelled")], default="draft", tracking=True, copy=False)
    awarded_id = fields.Many2one("purchase.order", string="Awarded to", readonly=True, copy=False)
    budget_amount = fields.Monetary(string="Labour budget of the items", compute="_compute_comparison")
    comparison_html = fields.Html(string="Comparison", compute="_compute_comparison", sanitize=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.sub.package") or _("New")
        return super().create(vals_list)

    def _labour_budget(self, line):
        costs = line.zc_cost_line_ids.filtered(lambda c: c.cost_type == "labour" and not c.product_id)
        return sum(c.unit_cost * (c.qty_per_unit or 1.0) for c in costs)

    @api.depends("rfq_ids.order_line.price_unit", "rfq_ids.state", "sale_line_ids")
    def _compute_comparison(self):
        for pkg in self:
            pkg.budget_amount = sum(self._labour_budget(l) * l.product_uom_qty for l in pkg.sale_line_ids)
            offers = pkg.rfq_ids.filtered(lambda p: p.state != "cancel").sorted("amount_untaxed")
            if not offers or not pkg.sale_line_ids:
                pkg.comparison_html = False
                continue
            head = "".join("<th class='text-end'>%s</th>" % escape(o.partner_id.name) for o in offers)
            rows = []
            for line in pkg.sale_line_ids:
                budget = self._labour_budget(line)
                cells = []
                for o in offers:
                    pol = o.order_line.filtered(lambda p, l=line: p.hom_boq_line_id == l)[:1]
                    price = pol.price_unit if pol else 0.0
                    cls = "text-danger" if budget and price > budget else ""
                    cells.append("<td class='text-end %s'>%s</td>" % (cls, "{:,.2f}".format(price) if pol else "-"))
                rows.append("<tr><td>%s</td><td class='text-end'>%s</td><td class='text-end'>%s</td>%s</tr>" % (
                    escape(line.product_id.display_name), "{:,.2f}".format(line.product_uom_qty),
                    "{:,.2f}".format(budget) if budget else "-", "".join(cells)))
            totals = "".join("<td class='text-end fw-bold %s'>%s</td>" % (
                "text-danger" if pkg.budget_amount and o.amount_untaxed > pkg.budget_amount else "text-success",
                "{:,.2f}".format(o.amount_untaxed)) for o in offers)
            pkg.comparison_html = Markup(
                "<table class='table table-sm'><thead><tr><th>%s</th><th class='text-end'>%s</th>"
                "<th class='text-end'>%s</th>%s</tr></thead><tbody>%s<tr><td colspan='2' class='fw-bold'>%s</td>"
                "<td class='text-end fw-bold'>%s</td>%s</tr></tbody></table>") % (
                _("Item"), _("Qty"), _("Budget rate"), Markup(head), Markup("".join(rows)), _("Total"),
                "{:,.2f}".format(pkg.budget_amount), Markup(totals))

    def action_request_offers(self):
        for pkg in self:
            if not pkg.partner_ids or not pkg.sale_line_ids:
                raise UserError(_("Choose the BOQ items and the subcontractors to invite."))
            account = pkg.sale_order_id.project_ids[:1].account_id
            for partner in pkg.partner_ids - pkg.rfq_ids.partner_id:
                self.env["purchase.order"].create({
                    "partner_id": partner.id, "hom_is_subcontract": True, "zc_package_id": pkg.id,
                    "origin": pkg.name,
                    "order_line": [Command.create({
                        "product_id": pkg.product_id.id, "name": "%s - %s" % (pkg.title, l.product_id.display_name),
                        "product_qty": l.product_uom_qty, "price_unit": 0.0, "hom_boq_line_id": l.id,
                        "analytic_distribution": {str(account.id): 100} if account else False})
                        for l in pkg.sale_line_ids]})
            pkg.state = "tender"

    def action_award(self):
        self.ensure_one()
        chosen = self.env["purchase.order"].browse(self.env.context.get("zc_award_po_id")) \
            if self.env.context.get("zc_award_po_id") else \
            self.rfq_ids.filtered(lambda p: p.state in ("draft", "sent") and p.amount_untaxed).sorted("amount_untaxed")[:1]
        if not chosen:
            raise UserError(_("Enter the prices of the offers first."))
        chosen.button_confirm()  # the item budget check (and PM exception) applies here
        (self.rfq_ids - chosen).filtered(lambda p: p.state in ("draft", "sent")).button_cancel()
        self.write({"state": "awarded", "awarded_id": chosen.id})
        self.message_post(body=_("Awarded to %(p)s for %(a)s.", p=chosen.partner_id.name, a=chosen.amount_untaxed))
        return chosen._get_records_action()

    def action_view_offers(self):
        return {"type": "ir.actions.act_window", "name": _("Offers"), "res_model": "purchase.order",
                "view_mode": "list,form", "domain": [("id", "in", self.rfq_ids.ids)]}


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    zc_package_id = fields.Many2one("zc.sub.package", string="Subcontract package", index="btree_not_null",
                                    copy=False)

    def action_zc_award(self):
        self.ensure_one()
        return self.zc_package_id.with_context(zc_award_po_id=self.id).action_award()
