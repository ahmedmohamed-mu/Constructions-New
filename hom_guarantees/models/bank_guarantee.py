from datetime import timedelta

from odoo import _, api, fields, models


class HomBankGuarantee(models.Model):
    _name = "hom.bank.guarantee"
    _description = "Letter of Guarantee"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "expiry_date, id"
    _check_company_auto = True

    name = fields.Char(string="Guarantee No.", required=True, tracking=True)
    direction = fields.Selection([
        ("issued", "Issued by us (to an owner)"),
        ("received", "Received (from a subcontractor / supplier)"),
    ], required=True, default="issued", tracking=True)
    guarantee_type = fields.Selection([
        ("bid", "Bid bond / ابتدائي"),
        ("advance", "Advance payment / دفعة مقدمة"),
        ("performance", "Performance / نهائي"),
        ("retention", "Retention / بديل محتجز"),
        ("other", "Other"),
    ], string="Type", required=True, default="performance", tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one("res.currency", required=True, default=lambda self: self.env.company.currency_id)
    partner_id = fields.Many2one("res.partner", string="Beneficiary / Counterparty", required=True, tracking=True)
    bank_name = fields.Char(string="Bank", tracking=True)
    sale_order_id = fields.Many2one("sale.order", string="Contract", check_company=True,
                                    domain="[('hom_project_type', '=', 'contracting')]")
    purchase_order_id = fields.Many2one("purchase.order", string="Subcontract", check_company=True,
                                        domain="[('hom_is_subcontract', '=', True)]")
    amount = fields.Monetary(string="Original Amount", required=True, tracking=True)
    amount_current = fields.Monetary(string="Current Amount", compute="_compute_amount_current", store=True,
                                     tracking=True, help="Advance guarantees reduce automatically as the advance is recovered.")
    commission_pct = fields.Float(string="Bank Commission %", help="Yearly commission, for cost tracking.")
    cash_margin = fields.Monetary(string="Cash Margin", help="Cash blocked by the bank against this guarantee.")
    issue_date = fields.Date(default=fields.Date.context_today, required=True)
    expiry_date = fields.Date(required=True, tracking=True)
    reminder_days = fields.Integer(default=30, help="Alert this many days before expiry.")
    responsible_id = fields.Many2one("res.users", string="Responsible", default=lambda self: self.env.user, tracking=True)
    state = fields.Selection([
        ("active", "Active"), ("extended", "Extended"), ("released", "Released"), ("expired", "Expired"),
    ], default="active", required=True, tracking=True)
    days_to_expiry = fields.Integer(compute="_compute_days_to_expiry")
    note = fields.Html()

    @api.depends("amount", "guarantee_type", "sale_order_id.invoice_ids.state", "purchase_order_id.invoice_ids.state")
    def _compute_amount_current(self):
        for g in self:
            current = g.amount
            if g.guarantee_type == "advance":
                outstanding = g._advance_outstanding()
                if outstanding is not None:
                    current = min(g.amount, max(outstanding, 0.0))
            g.amount_current = current

    def _advance_outstanding(self):
        """Advance still to be recovered on the linked contract, or None when unknown."""
        self.ensure_one()
        if self.direction == "issued" and self.sale_order_id:
            order = self.sale_order_id.sudo()
            total = 0.0
            for dp in order.order_line.filtered(lambda l: l.is_downpayment and not l.display_type):
                qty = 0.0
                for aml in dp.invoice_lines.filtered(lambda a: a.move_id.state == "posted"):
                    sign = -1 if aml.move_id.move_type == "out_refund" else 1
                    qty += sign * aml.quantity
                total += max(qty, 0.0) * dp.price_unit
            return total
        if self.direction == "received" and self.purchase_order_id and "hom_advance_remaining" in self.purchase_order_id._fields:
            return self.purchase_order_id.sudo().hom_advance_remaining
        return None

    def _compute_days_to_expiry(self):
        today = fields.Date.context_today(self)
        for g in self:
            g.days_to_expiry = (g.expiry_date - today).days if g.expiry_date else 0

    def action_release(self):
        self.write({"state": "released"})

    def action_extend(self):
        self.write({"state": "extended"})

    def action_refresh_amount(self):
        self._compute_amount_current()

    @api.model
    def _cron_check_expiry(self):
        today = fields.Date.context_today(self)
        self.search([("state", "in", ("active", "extended"))])._compute_amount_current()
        for g in self.search([("state", "in", ("active", "extended"))]):
            if g.expiry_date < today:
                g.state = "expired"
                g.message_post(body=_("Guarantee expired on %s.", g.expiry_date))
                continue
            if g.expiry_date <= today + timedelta(days=g.reminder_days or 30):
                summary = _("Guarantee %(n)s expires on %(d)s", n=g.name, d=g.expiry_date)
                if not g.activity_ids.filtered(lambda a: a.summary == summary):
                    action = _("extend it with the bank or ask the beneficiary to release it") if g.direction == "issued" \
                        else _("ask the subcontractor to extend it, or liquidate it before expiry")
                    g.activity_schedule("mail.mail_activity_data_todo", date_deadline=g.expiry_date - timedelta(days=7),
                                        user_id=(g.responsible_id or self.env.user).id, summary=summary,
                                        note=_("Amount %(a)s with %(b)s: %(act)s.", a="{:,.0f}".format(g.amount_current),
                                               b=g.bank_name or "-", act=action))


class SaleOrder(models.Model):
    _inherit = "sale.order"

    hom_guarantee_ids = fields.One2many("hom.bank.guarantee", "sale_order_id", string="Guarantees")
    hom_guarantee_count = fields.Integer(compute="_compute_hom_guarantee_count")

    def _compute_hom_guarantee_count(self):
        for order in self:
            order.hom_guarantee_count = len(order.hom_guarantee_ids)

    def action_hom_guarantees(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_guarantees.action_hom_bank_guarantee")
        action["domain"] = [("sale_order_id", "=", self.id)]
        action["context"] = {"default_sale_order_id": self.id, "default_partner_id": self.partner_id.id,
                             "default_direction": "issued"}
        return action


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    hom_guarantee_ids = fields.One2many("hom.bank.guarantee", "purchase_order_id", string="Guarantees")
    hom_guarantee_count = fields.Integer(compute="_compute_hom_guarantee_count")

    def _compute_hom_guarantee_count(self):
        for order in self:
            order.hom_guarantee_count = len(order.hom_guarantee_ids)

    def action_hom_guarantees(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_guarantees.action_hom_bank_guarantee")
        action["domain"] = [("purchase_order_id", "=", self.id)]
        action["context"] = {"default_purchase_order_id": self.id, "default_partner_id": self.partner_id.id,
                             "default_direction": "received"}
        return action
