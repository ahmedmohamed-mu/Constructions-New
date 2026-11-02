from odoo import _, api, models
from odoo.tools import float_compare


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    # A subcontract line is tied to its BOQ item through hom_boq_line_id (back-to-back). The budget check and the
    # item card read zc_boq_line_id: keep both on the same item so a new subcontract is checked like a material PO.
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("hom_boq_line_id") and not vals.get("zc_boq_line_id"):
                vals["zc_boq_line_id"] = vals["hom_boq_line_id"]
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("hom_boq_line_id") and "zc_boq_line_id" not in vals:
            vals = dict(vals, zc_boq_line_id=vals["hom_boq_line_id"])
        return super().write(vals)

    def _compute_zc_check(self):
        super()._compute_zc_check()
        # Installation / labour budget lines usually have no product: a subcontract line of the same item is
        # checked against them (price per BOQ unit, quantity = the item quantity).
        for line in self.filtered(lambda l: l.zc_check == "no_budget" and l.order_id.hom_is_subcontract):
            boq = line.zc_boq_line_id
            costs = boq.zc_cost_line_ids.filtered(lambda c: not c.product_id and c.cost_type == "labour")
            if len(costs) != 1:
                continue
            cost = costs
            budget_price = cost.unit_cost * (cost.qty_per_unit or 1.0)
            others = self.sudo().search([
                ("zc_boq_line_id", "=", boq.id), ("zc_cost_line_id", "=", cost.id),
                ("order_id.state", "in", ("purchase", "done")), ("id", "not in", line._origin.ids)])
            already = sum(others.mapped("product_qty"))
            remaining = max(cost.allowed_qty - already, 0.0)
            tolerance = line.company_id.zc_price_tolerance_pct or 0.0
            over_qty = float_compare(line.product_qty, remaining, precision_digits=2) > 0
            over_price = bool(budget_price) and float_compare(
                line.price_unit, budget_price * (1 + tolerance / 100.0), precision_digits=2) > 0
            notes = []
            if over_qty:
                notes.append(_("Qty %(q)s > item quantity still open %(a)s", q=line.product_qty, a=remaining))
            if over_price:
                notes.append(_("Rate %(p)s > installation budget %(b)s", p=line.price_unit, b=budget_price))
            line.zc_cost_line_id = cost
            line.zc_allowed_remaining = remaining
            line.zc_budget_price = budget_price
            line.zc_check = "over_both" if over_qty and over_price else "over_qty" if over_qty else \
                "over_price" if over_price else "ok"
            line.zc_check_note = " · ".join(notes) if notes else False


class ZcBoqCostLine(models.Model):
    _inherit = "zc.boq.cost.line"

    def _purchase_lines(self):
        self.ensure_one()
        if self.product_id:
            return super()._purchase_lines()
        if not self.id:
            return self.env["purchase.order.line"]
        return self.env["purchase.order.line"].sudo().search([
            ("zc_cost_line_id", "=", self.id), ("order_id.state", "in", ("purchase", "done"))])
