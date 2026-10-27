from datetime import timedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

DAYS = [("d1", "Sat"), ("d2", "Sun"), ("d3", "Mon"), ("d4", "Tue"), ("d5", "Wed"), ("d6", "Thu"), ("d7", "Fri")]


class ZcLabourSheet(models.Model):
    """Weekly attendance of the small daily labour on a site, paid in cash at the end of the week.

    Approved by the project manager, it becomes one vendor bill (the payee is the foreman who pays the
    workers) charged to the project analytic account.
    """
    _name = "zc.labour.sheet"
    _description = "Weekly Daily-Labour Sheet"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "week_start desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    project_id = fields.Many2one("project.project", required=True, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    week_start = fields.Date(string="Week Starting (Saturday)", required=True,
                             default=lambda self: self._default_week_start())
    week_end = fields.Date(compute="_compute_name", store=True)
    user_id = fields.Many2one("res.users", string="Prepared by", default=lambda self: self.env.user)
    partner_id = fields.Many2one("res.partner", string="Payee",
                                 default=lambda self: self.env.company.zc_labour_partner_id)
    line_ids = fields.One2many("zc.labour.sheet.line", "sheet_id", copy=True)
    total_days = fields.Float(compute="_compute_totals", store=True)
    amount_total = fields.Monetary(compute="_compute_totals", store=True)
    bill_id = fields.Many2one("account.move", readonly=True, copy=False)
    bill_payment_state = fields.Selection(related="bill_id.payment_state")
    state = fields.Selection([("draft", "Draft"), ("submitted", "Waiting PM"), ("approved", "Approved"),
                              ("billed", "Billed")], default="draft", required=True, tracking=True, copy=False)

    @api.model
    def _default_week_start(self):
        today = fields.Date.context_today(self)
        return today - timedelta(days=(today.weekday() - 5) % 7)  # last Saturday

    @api.depends("project_id.name", "week_start")
    def _compute_name(self):
        for sheet in self:
            sheet.week_end = sheet.week_start + timedelta(days=6) if sheet.week_start else False
            sheet.name = _("Labour %(p)s - week %(w)s", p=sheet.project_id.name or "", w=sheet.week_start or "")

    @api.depends("line_ids.days", "line_ids.amount")
    def _compute_totals(self):
        for sheet in self:
            sheet.total_days = sum(sheet.line_ids.mapped("days"))
            sheet.amount_total = sum(sheet.line_ids.mapped("amount"))

    def action_submit(self):
        for sheet in self.filtered(lambda s: s.state == "draft"):
            if not sheet.line_ids:
                raise UserError(_("Add the workers of the week first."))
            sheet.state = "submitted"
            pm = sheet.project_id.user_id
            if pm:
                sheet.activity_schedule("mail.mail_activity_data_todo", user_id=pm.id,
                                        summary=_("Approve the labour sheet %s", sheet.name),
                                        note=_("%(d)s days, %(a)s.", d=sheet.total_days, a=sheet.amount_total))

    def action_approve(self):
        for sheet in self.filtered(lambda s: s.state == "submitted"):
            sheet.state = "approved"
            sheet.activity_feedback(["mail.mail_activity_data_todo"])
            sheet._create_bill()

    def action_reset(self):
        self.filtered(lambda s: s.state == "submitted").state = "draft"

    def _create_bill(self):
        self.ensure_one()
        company = self.company_id
        product = company.zc_labour_product_id
        partner = self.partner_id or company.zc_labour_partner_id
        if not product or not partner:
            raise UserError(_("Set the daily labour service and payee in Settings > Contracting first."))
        if not self.project_id.account_id:
            raise UserError(_("The project has no analytic account."))
        dist = {str(self.project_id.account_id.id): 100}
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": partner.id, "invoice_date": self.week_end,
            "ref": self.name,
            "invoice_line_ids": [Command.create({
                "product_id": product.id, "name": "%s - %s" % (l.worker, l.trade or ""),
                "quantity": l.days, "price_unit": l.daily_wage, "tax_ids": [Command.clear()],
                "analytic_distribution": dist,
            }) for l in self.line_ids if l.days],
        })
        bill.action_post()
        self.write({"bill_id": bill.id, "state": "billed"})
        self.message_post(body=_("Vendor bill %s posted, pay it from the site cash.", bill.display_name))
        return bill

    def action_view_bill(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.bill_id.id,
                "view_mode": "form"}


class ZcLabourSheetLine(models.Model):
    _name = "zc.labour.sheet.line"
    _description = "Weekly Daily-Labour Sheet - Worker"

    sheet_id = fields.Many2one("zc.labour.sheet", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="sheet_id.currency_id")
    worker = fields.Char(required=True)
    trade = fields.Char()
    d1 = fields.Float("Sat", default=0.0)
    d2 = fields.Float("Sun", default=0.0)
    d3 = fields.Float("Mon", default=0.0)
    d4 = fields.Float("Tue", default=0.0)
    d5 = fields.Float("Wed", default=0.0)
    d6 = fields.Float("Thu", default=0.0)
    d7 = fields.Float("Fri", default=0.0)
    days = fields.Float(compute="_compute_amount", store=True)
    daily_wage = fields.Monetary(required=True)
    amount = fields.Monetary(compute="_compute_amount", store=True)

    @api.depends("d1", "d2", "d3", "d4", "d5", "d6", "d7", "daily_wage")
    def _compute_amount(self):
        for line in self:
            line.days = sum(line[f] for f, _label in DAYS)
            line.amount = line.days * line.daily_wage

    @api.constrains("d1", "d2", "d3", "d4", "d5", "d6", "d7")
    def _check_days(self):
        for line in self:
            if any(line[f] < 0 or line[f] > 1.5 for f, _label in DAYS):
                raise UserError(_("A day is between 0 and 1.5 (overtime)."))
