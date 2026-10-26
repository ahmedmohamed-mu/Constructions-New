from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_round


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    zc_boq_line_id = fields.Many2one(
        "sale.order.line", string="For BOQ Item", index=True, copy=True,
        domain="[('order_id.state', '=', 'sale'), ('display_type', '=', False), ('is_downpayment', '=', False)]",
        help="The contract item this material is bought for. The quantity is checked against the approved shop "
             "drawing and the price against the item budget.")
    zc_cost_line_id = fields.Many2one("zc.boq.cost.line", string="Budget Line", compute="_compute_zc_check", store=True)
    zc_allowed_remaining = fields.Float(string="Still Allowed", compute="_compute_zc_check", store=True,
                                        digits="Product Unit")
    zc_budget_price = fields.Float(string="Budget Price", compute="_compute_zc_check", store=True,
                                   digits="Product Price")
    zc_check = fields.Selection([("ok", "Within budget"), ("no_budget", "No item budget"),
                                 ("over_qty", "Above shop drawing"), ("over_price", "Above budget price"),
                                 ("over_both", "Above quantity and price")],
                                string="Check", compute="_compute_zc_check", store=True)
    zc_check_note = fields.Char(string="Check Detail", compute="_compute_zc_check", store=True)

    @api.depends("zc_boq_line_id", "product_id", "product_qty", "price_unit", "order_id.state",
                 "zc_boq_line_id.zc_sd_qty", "zc_boq_line_id.zc_cost_line_ids.unit_cost",
                 "zc_boq_line_id.zc_cost_line_ids.qty_per_unit")
    def _compute_zc_check(self):
        for line in self:
            line.zc_cost_line_id = False
            line.zc_allowed_remaining = 0.0
            line.zc_budget_price = 0.0
            line.zc_check = False
            line.zc_check_note = False
            boq = line.zc_boq_line_id
            if not boq or not line.product_id:
                continue
            cost = boq.zc_cost_line_ids.filtered(lambda c: c.product_id == line.product_id)[:1]
            if not cost:
                line.zc_check = "no_budget"
                line.zc_check_note = _("%s has no budget line for this material.", boq.product_id.display_name)
                continue
            line.zc_cost_line_id = cost
            others = self.sudo().search([
                ("zc_boq_line_id", "=", boq.id), ("product_id", "=", line.product_id.id),
                ("order_id.state", "in", ("purchase", "done")), ("id", "not in", line._origin.ids)])
            already = sum(others.mapped("product_qty"))
            digits = self.env["decimal.precision"].precision_get("Product Unit")
            remaining = float_round(max(cost.allowed_qty - already, 0.0), precision_digits=digits)
            line.zc_allowed_remaining = remaining
            line.zc_budget_price = cost.unit_cost
            tolerance = line.company_id.zc_price_tolerance_pct or 0.0
            over_qty = float_compare(line.product_qty, remaining, precision_digits=digits) > 0
            over_price = bool(cost.unit_cost) and float_compare(
                line.price_unit, cost.unit_cost * (1 + tolerance / 100.0), precision_digits=2) > 0
            notes = []
            if over_qty:
                notes.append(_("Qty %(q)s > allowed %(a)s (shop drawing %(sd)s + %(t)s%%, already ordered %(o)s)",
                               q=line.product_qty, a=remaining, sd=cost.budget_qty, t=cost.tolerance_pct, o=already))
            if over_price:
                notes.append(_("Price %(p)s > budget %(b)s", p=line.price_unit, b=cost.unit_cost))
            line.zc_check = "over_both" if over_qty and over_price else "over_qty" if over_qty else \
                "over_price" if over_price else "ok"
            line.zc_check_note = " · ".join(notes) if notes else False

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.filtered(lambda l: not l.zc_boq_line_id and l.product_id)._zc_guess_boq_line()
        return lines

    def _zc_guess_boq_line(self):
        """Link the line to the BOQ item of its project when the material appears in exactly one item budget."""
        for line in self:
            accounts = set()
            for key in (line.analytic_distribution or {}):
                accounts.update(int(a) for a in str(key).split(",") if a.isdigit())
            if not accounts:
                continue
            projects = self.env["project.project"].sudo().search([("account_id", "in", list(accounts))])
            orders = projects.mapped("sale_order_id") | projects.mapped("reinvoiced_sale_order_id")
            if not orders:
                continue
            costs = self.env["zc.boq.cost.line"].sudo().search([("order_id", "in", orders.ids),
                                                                ("product_id", "=", line.product_id.id)])
            if len(costs.sale_line_id) == 1:
                line.zc_boq_line_id = costs.sale_line_id

    def _zc_stock_elsewhere(self):
        """Quantity of this material sitting in other stores of the company (to transfer instead of buying)."""
        self.ensure_one()
        if not self.product_id.is_storable:
            return []
        dest_wh = self.order_id.picking_type_id.warehouse_id
        quants = self.env["stock.quant"].sudo().search([
            ("product_id", "=", self.product_id.id), ("location_id.usage", "=", "internal"),
            ("company_id", "=", self.company_id.id), ("quantity", ">", 0)])
        out = {}
        for q in quants:
            wh = q.location_id.warehouse_id
            if wh and wh != dest_wh:
                out[wh] = out.get(wh, 0.0) + q.quantity - q.reserved_quantity
        return [(wh, qty) for wh, qty in out.items() if qty > 0]


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    zc_exception_state = fields.Selection([("none", "Not needed"), ("requested", "Requested"),
                                           ("approved", "Approved")], string="PM Exception", default="none",
                                          copy=False, tracking=True)
    zc_exception_reason = fields.Text(string="Exception Reason", copy=False, tracking=True)
    zc_exception_user_id = fields.Many2one("res.users", string="Approved by", copy=False, readonly=True)
    zc_needs_exception = fields.Boolean(compute="_compute_zc_summary")
    zc_check_html = fields.Html(string="Material Check", compute="_compute_zc_summary", sanitize=False)

    @api.depends("order_line.zc_check", "order_line.zc_check_note", "order_line.product_id", "order_line.product_qty")
    def _compute_zc_summary(self):
        for order in self:
            flagged = order.order_line.filtered(lambda l: l.zc_check in ("over_qty", "over_price", "over_both"))
            order.zc_needs_exception = bool(flagged)
            rows = []
            for line in order.order_line.filtered(lambda l: l.zc_check):
                icon = "✔" if line.zc_check == "ok" else ("•" if line.zc_check == "no_budget" else "✖")
                text = line.zc_check_note or _("%(p)s: within the shop drawing quantity and the item budget",
                                               p=line.product_id.display_name)
                if line.zc_check != "ok" and line.zc_check_note:
                    text = "%s: %s" % (line.product_id.display_name, line.zc_check_note)
                rows.append(Markup("<li>%s %s</li>") % (icon, text))
                if line.state in ("draft", "sent") and line.zc_check in ("over_qty", "over_both"):
                    for wh, qty in line._zc_stock_elsewhere():
                        rows.append(Markup("<li>↪ %s</li>") % _(
                            "%(q)s %(u)s available in %(w)s: transfer instead of buying",
                            q=qty, u=line.uom_id.name, w=wh.name))
            order.zc_check_html = Markup("<ul class='mb-0'>%s</ul>") % Markup("").join(rows) if rows else False

    def _zc_project_managers(self):
        accounts = set()
        for line in self.order_line:
            for key in (line.analytic_distribution or {}):
                accounts.update(int(a) for a in str(key).split(",") if a.isdigit())
        Project = self.env["project.project"].sudo()
        projects = Project.search([("account_id", "in", list(accounts))]) if accounts else Project
        projects |= self.order_line.sudo().zc_boq_line_id.order_id.project_ids
        users = projects.user_id
        return users or self.company_id.hom_management_user_id

    def button_confirm(self):
        for order in self:
            order.order_line._compute_zc_check()
            if order.zc_needs_exception and order.zc_exception_state != "approved":
                lines = order.order_line.filtered(lambda l: l.zc_check in ("over_qty", "over_price", "over_both"))
                details = "\n".join("- %s: %s" % (l.product_id.display_name, l.zc_check_note) for l in lines)
                raise UserError(_("This order is above the approved shop drawing or the item budget:\n%(d)s\n\n"
                                  "Reduce it, or click 'Request PM Exception' with the reason.", d=details))
        return super().button_confirm()

    def action_zc_request_exception(self):
        for order in self:
            if not order.zc_exception_reason:
                raise UserError(_("Write the reason for the exception first (e.g. waste on site, model change)."))
            order.zc_exception_state = "requested"
            for user in order._zc_project_managers():
                order.activity_schedule("mail.mail_activity_data_todo", user_id=user.id,
                                        summary=_("Approve purchase exception %s", order.name),
                                        note=Markup("%s<br/>%s") % (escape(order.zc_exception_reason),
                                                                     order.zc_check_html or ""))
        return True

    def action_zc_approve_exception(self):
        for order in self:
            managers = order._zc_project_managers()
            allowed = self.env.user in managers or self.env.user.has_group("purchase.group_purchase_manager") \
                or self.env.is_superuser()
            if not allowed:
                raise UserError(_("Only the project manager can approve this exception."))
            order.write({"zc_exception_state": "approved", "zc_exception_user_id": self.env.user.id})
            order.activity_feedback(["mail.mail_activity_data_todo"],
                                    feedback=_("Exception approved by %s", self.env.user.name))
            order.message_post(body=_("Purchase exception approved by %(u)s. Reason: %(r)s",
                                      u=self.env.user.name, r=order.zc_exception_reason or ""))
        return True

    def action_zc_reject_exception(self):
        for order in self:
            order.write({"zc_exception_state": "none"})
            order.activity_feedback(["mail.mail_activity_data_todo"], feedback=_("Exception rejected"))
            order.message_post(body=_("Purchase exception rejected by %s.", self.env.user.name))
        return True
