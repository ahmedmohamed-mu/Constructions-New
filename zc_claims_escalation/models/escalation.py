from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class ZcPriceIndex(models.Model):
    """Published price index (e.g. CAPMAS index of steel, cement, wages, fuel)."""
    _name = "zc.price.index"
    _description = "Price Index"
    _order = "code"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    source = fields.Char(default="CAPMAS", help="Publisher of the index (e.g. CAPMAS bulletin).")
    value_ids = fields.One2many("zc.price.index.value", "index_id", string="Values")
    last_value = fields.Float(compute="_compute_last", digits=(16, 4))
    last_date = fields.Date(compute="_compute_last")

    def _compute_last(self):
        for rec in self:
            last = rec.value_ids.sorted("date")[-1:]
            rec.last_value, rec.last_date = last.value, last.date

    def _value_at(self, date):
        self.ensure_one()
        values = self.value_ids.filtered(lambda v: v.date <= date).sorted("date")
        if not values:
            raise UserError(_("No value of the index %(i)s on or before %(d)s.", i=self.name, d=date))
        return values[-1].value


class ZcPriceIndexValue(models.Model):
    _name = "zc.price.index.value"
    _description = "Price Index Value"
    _order = "index_id, date desc"

    index_id = fields.Many2one("zc.price.index", required=True, ondelete="cascade")
    date = fields.Date(required=True, help="Month of the published value.")
    value = fields.Float(required=True, digits=(16, 4))

    _uniq_index_date = models.Constraint("unique(index_id, date)", "One value per index and date.")


class ZcEscalationFormula(models.Model):
    """Weight of each index in the contract price (the rest is fixed)."""
    _name = "zc.escalation.formula"
    _description = "Price Escalation Formula Line"

    sale_order_id = fields.Many2one("sale.order", required=True, ondelete="cascade", index=True)
    index_id = fields.Many2one("zc.price.index", required=True)
    weight = fields.Float(string="Weight %", required=True)
    base_value = fields.Float(compute="_compute_base_value", digits=(16, 4))

    @api.depends("index_id.value_ids", "sale_order_id.zc_escalation_base_date")
    def _compute_base_value(self):
        for rec in self:
            date = rec.sale_order_id.zc_escalation_base_date
            values = rec.index_id.value_ids.filtered(lambda v: date and v.date <= date).sorted("date")
            rec.base_value = values[-1:].value


class SaleOrder(models.Model):
    _inherit = "sale.order"

    zc_escalation = fields.Boolean(string="Price escalation clause")
    zc_escalation_base_date = fields.Date(string="Escalation base date",
                                          help="Usually the tender submission date (base month of the indices).")
    zc_escalation_threshold = fields.Float(string="Threshold %",
                                           help="Adjust only when the weighted change exceeds this percentage.")
    zc_escalation_exclude_advance = fields.Boolean(string="Exclude work financed by the advance", default=True)
    zc_escalation_formula_ids = fields.One2many("zc.escalation.formula", "sale_order_id", string="Escalation formula")
    zc_escalation_fixed = fields.Float(string="Fixed share %", compute="_compute_zc_escalation_fixed")
    zc_escalation_ids = fields.One2many("zc.escalation", "sale_order_id", string="Price escalations")
    zc_claim_ids = fields.One2many("zc.claim", "sale_order_id", string="Claims")
    zc_claim_count = fields.Integer(compute="_compute_zc_counts")
    zc_escalation_count = fields.Integer(compute="_compute_zc_counts")

    @api.depends("zc_escalation_formula_ids.weight")
    def _compute_zc_escalation_fixed(self):
        for order in self:
            order.zc_escalation_fixed = 100.0 - sum(order.zc_escalation_formula_ids.mapped("weight"))

    def _compute_zc_counts(self):
        for order in self:
            order.zc_claim_count = len(order.zc_claim_ids)
            order.zc_escalation_count = len(order.zc_escalation_ids)

    @api.constrains("zc_escalation_formula_ids")
    def _check_zc_formula(self):
        for order in self:
            if sum(order.zc_escalation_formula_ids.mapped("weight")) > 100.0001:
                raise UserError(_("The index weights of %s exceed 100%%.", order.name))

    def action_zc_claims(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Claims"), "res_model": "zc.claim",
                "view_mode": "list,form", "domain": [("sale_order_id", "=", self.id)],
                "context": {"default_sale_order_id": self.id}}

    def action_zc_escalations(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "name": _("Price escalations"), "res_model": "zc.escalation",
                "view_mode": "list,form", "domain": [("sale_order_id", "=", self.id)],
                "context": {"default_sale_order_id": self.id}}


class ZcEscalation(models.Model):
    """Price escalation claim (فروق أسعار) for the work certified in a period."""
    _name = "zc.escalation"
    _description = "Price Escalation"
    _inherit = ["mail.thread"]
    _order = "date_to desc, id desc"
    _check_company_auto = True

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True,
                                    domain="[('zc_escalation', '=', True), ('state', '=', 'sale')]")
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id", store=True)
    partner_id = fields.Many2one(related="sale_order_id.partner_id", store=True, string="Owner")
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True, help="The index values of this month are used.")
    state = fields.Selection([("draft", "Draft"), ("submitted", "Submitted to Consultant"),
                              ("approved", "Approved"), ("invoiced", "Invoiced"), ("cancel", "Cancelled")],
                             default="draft", required=True, tracking=True, copy=False)
    certificate_ids = fields.Many2many("hom.progress.certificate", string="Certificates of the period", readonly=True)
    work_value = fields.Monetary(string="Work certified in the period", readonly=True)
    advance_excluded = fields.Monetary(string="Financed by the advance", readonly=True)
    base_value = fields.Monetary(string="Value subject to adjustment", readonly=True)
    factor = fields.Float(string="Adjustment factor", digits=(16, 6), readonly=True,
                          help="Σ weight × (current index / base index) + fixed share - 1")
    amount = fields.Monetary(string="Price difference", readonly=True, tracking=True)
    line_ids = fields.One2many("zc.escalation.line", "escalation_id", readonly=True)
    note = fields.Html()
    invoice_id = fields.Many2one("account.move", readonly=True, copy=False)

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for rec in self:
            rec.project_id = rec.sale_order_id.project_ids[:1]

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.escalation") or _("New")
        return super().create(vals_list)

    def action_compute(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only a draft escalation is recomputed."))
            order = rec.sale_order_id
            if not order.zc_escalation_base_date or not order.zc_escalation_formula_ids:
                raise UserError(_("Set the escalation base date and formula on %s.", order.name))
            certs = order.hom_certificate_ids.filtered(
                lambda c: c.state in ("approved", "invoiced") and rec.date_from <= (c.period_end or c.date) <= rec.date_to)
            work = sum(certs.mapped("amount_current"))
            advance = sum(certs.mapped(lambda c: -c.amount_advance_recovery)) if order.zc_escalation_exclude_advance else 0.0
            base = work - advance
            lines = []
            factor = order.zc_escalation_fixed / 100.0
            for f in order.zc_escalation_formula_ids:
                b = f.index_id._value_at(order.zc_escalation_base_date)
                c = f.index_id._value_at(rec.date_to)
                ratio = c / b if b else 1.0
                factor += f.weight / 100.0 * ratio
                lines.append(Command.create({"index_id": f.index_id.id, "weight": f.weight, "base_index": b,
                                             "current_index": c,
                                             "amount": rec.currency_id.round(base * f.weight / 100.0 * (ratio - 1))}))
            change = factor - 1.0
            if abs(change) * 100.0 <= (order.zc_escalation_threshold or 0.0):
                change = 0.0
            rec.line_ids.unlink()
            rec.write({"certificate_ids": [Command.set(certs.ids)], "work_value": work, "advance_excluded": advance,
                       "base_value": base, "factor": change, "line_ids": lines,
                       "amount": rec.currency_id.round(base * change)})

    def action_submit(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            if not rec.line_ids:
                rec.action_compute()
            rec.state = "submitted"

    def action_approve(self):
        self.filtered(lambda r: r.state == "submitted").write({"state": "approved"})

    def action_cancel(self):
        for rec in self:
            if rec.invoice_id and rec.invoice_id.state == "posted":
                raise UserError(_("The invoice of %s is posted.", rec.name))
            rec.state = "cancel"

    def action_draft(self):
        self.filtered(lambda r: r.state in ("submitted", "cancel")).write({"state": "draft"})

    def action_create_invoice(self):
        self.ensure_one()
        if self.state != "approved" or not self.amount:
            raise UserError(_("Only an approved, non-zero escalation is invoiced."))
        product = self.company_id.zc_escalation_product_id
        if not product:
            raise UserError(_("Set the price escalation product (code ZC-ESC)."))
        order = self.sale_order_id
        line = {"product_id": product.id, "quantity": 1, "price_unit": abs(self.amount),
                "name": _("%(n)s - price escalation %(f)s to %(t)s (factor %(x).4f%%)", n=self.name, f=self.date_from,
                          t=self.date_to, x=self.factor * 100),
                "tax_ids": [Command.set(product.taxes_id.filtered(lambda t: t.company_id == self.company_id).ids)]}
        if self.project_id.account_id:
            line["analytic_distribution"] = {str(self.project_id.account_id.id): 100}
        move = self.env["account.move"].create({
            "move_type": "out_invoice" if self.amount > 0 else "out_refund",
            "partner_id": order.partner_invoice_id.id, "invoice_origin": order.name,
            "ref": _("Price escalation %s", self.name), "invoice_line_ids": [Command.create(line)]})
        self.write({"invoice_id": move.id, "state": "invoiced"})
        return move._get_records_action()

    def action_view_invoice(self):
        return self.invoice_id._get_records_action()


class ZcEscalationLine(models.Model):
    _name = "zc.escalation.line"
    _description = "Price Escalation Line"

    escalation_id = fields.Many2one("zc.escalation", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="escalation_id.currency_id")
    index_id = fields.Many2one("zc.price.index", required=True)
    weight = fields.Float(string="Weight %")
    base_index = fields.Float(digits=(16, 4))
    current_index = fields.Float(digits=(16, 4))
    change_pct = fields.Float(string="Change %", compute="_compute_change")
    amount = fields.Monetary()

    @api.depends("base_index", "current_index")
    def _compute_change(self):
        for rec in self:
            rec.change_pct = ((rec.current_index / rec.base_index) - 1) * 100.0 if rec.base_index else 0.0
