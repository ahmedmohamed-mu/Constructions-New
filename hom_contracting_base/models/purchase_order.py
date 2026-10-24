from markupsafe import Markup

from odoo import Command, _, fields, models
from odoo.exceptions import UserError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    hom_is_subcontract = fields.Boolean(string="Subcontract", tracking=True)
    hom_advance_amount = fields.Monetary(string="Advance Amount", currency_field="currency_id", tracking=True)
    hom_advance_recovery_pct = fields.Float(string="Advance Recovery %", default=10.0)
    hom_retention_pct = fields.Float(string="Retention %", default=5.0)
    hom_retention_release_ids = fields.One2many("account.move", "hom_retention_purchase_id", string="Retention Releases")
    hom_retention_held = fields.Monetary(string="Retention Held", compute="_compute_hom_retention")
    hom_retention_released = fields.Monetary(string="Retention Released", compute="_compute_hom_retention")
    hom_retention_balance = fields.Monetary(string="Retention to Pay", compute="_compute_hom_retention")
    hom_budget_warning = fields.Html(readonly=True, copy=False)

    def _compute_hom_retention(self):
        for order in self:
            product = order.company_id.hom_retention_sub_product_id
            held = released = 0.0
            if product:
                for move in order.invoice_ids.filtered(lambda m: m.state == "posted"):
                    sign = -1 if move.move_type == "in_refund" else 1
                    held -= sign * sum(move.invoice_line_ids.filtered(lambda l: l.product_id == product).mapped("price_subtotal"))
                for move in order.hom_retention_release_ids.filtered(lambda m: m.state == "posted"):
                    released += sum(move.invoice_line_ids.filtered(lambda l: l.product_id == product).mapped("price_subtotal"))
            order.hom_retention_held = held
            order.hom_retention_released = released
            order.hom_retention_balance = held - released

    def action_hom_release_retention(self):
        self.ensure_one()
        product = self.company_id.hom_retention_sub_product_id
        amount = self.hom_retention_balance
        if not product or amount <= 0:
            raise UserError(_("There is no retention left to pay on this subcontract."))
        if self.hom_retention_release_ids.filtered(lambda m: m.state == "draft"):
            raise UserError(_("A draft retention release bill already exists."))
        move = self.env["account.move"].create({
            "move_type": "in_invoice",
            "partner_id": self.partner_id.id,
            "currency_id": self.currency_id.id,
            "invoice_origin": self.name,
            "ref": _("Retention release - %s", self.name),
            "hom_retention_purchase_id": self.id,
            "invoice_line_ids": [Command.create({
                "product_id": product.id,
                "name": _("Release of retention - %s", self.name),
                "quantity": 1,
                "price_unit": amount,
                "tax_ids": [Command.clear()],
            })],
        })
        self.message_post(body=_("Retention release bill %s prepared.", move.display_name))
        return move._get_records_action()

    # ------------------------------------------------------------------
    # Budget control
    # ------------------------------------------------------------------
    def button_confirm(self):
        res = super().button_confirm()
        for order in self.filtered(lambda o: o.state == "purchase"):
            order._hom_check_budget()
        return res

    def _hom_budget_lines(self):
        """Confirmed budget lines whose analytic accounts all appear on this order."""
        self.ensure_one()
        if "budget.line" not in self.env:
            return self.env["account.analytic.account"].browse()
        account_ids = set()
        for line in self.order_line:
            for key in (line.analytic_distribution or {}):
                account_ids.update(int(a) for a in str(key).split(","))
        if not account_ids:
            return self.env["budget.line"]
        Line = self.env["budget.line"].sudo()
        columns = [name for name, field in Line._fields.items()
                   if field.type == "many2one" and field.comodel_name == "account.analytic.account"
                   and field.store and name != "auto_account_id"]
        candidates = Line.search([("budget_analytic_id.state", "=", "confirmed"),
                                  ("budget_analytic_id.budget_type", "!=", "revenue")])
        result = Line
        for bl in candidates:
            set_ids = {bl[col].id for col in columns if bl[col]}
            if set_ids and set_ids <= account_ids:
                result |= bl
        return result

    def _hom_check_budget(self):
        self.ensure_one()
        try:
            lines = self._hom_budget_lines()
        except Exception:  # never block a purchase because of reporting
            return
        over = lines.filtered(lambda l: l.budget_amount and l.committed_amount > l.budget_amount)
        if not over:
            return
        rows = []
        for bl in over:
            label = " / ".join(bl[c].display_name for c in ("account_id", "x_plan2_id") if c in bl._fields and bl[c]) \
                or bl.budget_analytic_id.display_name
            rows.append(_("%(line)s: committed %(c)s > budget %(b)s (+%(p).1f%%)", line=label,
                          c="{:,.0f}".format(bl.committed_amount), b="{:,.0f}".format(bl.budget_amount),
                          p=(bl.committed_amount / bl.budget_amount - 1) * 100))
        text = Markup("<br/>").join(rows)
        self.hom_budget_warning = text
        self.message_post(body=Markup("%s<br/>%s") % (_("Budget overrun after confirming this order:"), text))
        users = self.env["res.users"]
        projects = self.env["project.project"].sudo().search([("account_id", "in", over.mapped("account_id").ids)]) \
            if "account_id" in over._fields else self.env["project.project"]
        users |= projects.user_id
        users |= self.company_id.hom_management_user_id
        for user in users:
            self.activity_schedule("mail.mail_activity_data_warning", user_id=user.id,
                                   summary=_("Budget overrun on %s", self.name), note=text)
