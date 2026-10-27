from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero


class MaintenanceEquipment(models.Model):
    _inherit = "maintenance.equipment"

    zc_daily_rate = fields.Monetary(string="Internal Daily Rent", currency_field="zc_currency_id",
                                    help="Rate charged to a project for each working day of the machine.")
    zc_currency_id = fields.Many2one(related="company_id.currency_id")
    zc_analytic_account_id = fields.Many2one(
        "account.analytic.account", string="Equipment Cost Centre",
        help="Real costs of the machine (fuel, repairs, depreciation) and the rent it recovers from projects "
             "both land here: the balance shows whether the internal rate covers the machine.")
    zc_usage_ids = fields.One2many("zc.equipment.usage", "equipment_id", string="Usage Log")

    @api.model
    def _zc_equipment_plan(self):
        Plan = self.env["account.analytic.plan"].sudo()
        plan = Plan.search([("name", "in", ("Equipment", "المعدات"))], limit=1)
        return plan or Plan.create({"name": "Equipment"})

    def action_zc_create_cost_centre(self):
        plan = self._zc_equipment_plan()
        for eq in self.filtered(lambda e: not e.zc_analytic_account_id):
            eq.zc_analytic_account_id = self.env["account.analytic.account"].sudo().create({
                "name": eq.name, "code": eq.serial_no or False, "plan_id": plan.id,
                "company_id": eq.company_id.id or False})
        return True


class ZcEquipmentUsage(models.Model):
    """Daily log of company equipment working on a project (filled by the site / the yard)."""
    _name = "zc.equipment.usage"
    _description = "Equipment Usage"
    _order = "date desc, id desc"

    equipment_id = fields.Many2one("maintenance.equipment", required=True, index=True)
    project_id = fields.Many2one("project.project", required=True, index=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date = fields.Date(required=True, default=fields.Date.context_today)
    days = fields.Float(default=1.0, digits=(16, 2), help="Working days (half day = 0.5).")
    rate = fields.Monetary(string="Daily Rent", compute="_compute_rate", store=True, readonly=False)
    amount = fields.Monetary(compute="_compute_amount", store=True)
    operator = fields.Char()
    note = fields.Char()
    charge_id = fields.Many2one("zc.equipment.charge", readonly=True, copy=False)
    state = fields.Selection([("open", "Not charged"), ("charged", "Charged")], compute="_compute_state",
                             store=True)

    @api.depends("equipment_id.zc_daily_rate")
    def _compute_rate(self):
        for usage in self:
            if not usage.rate or not usage.charge_id:
                usage.rate = usage.equipment_id.zc_daily_rate

    @api.depends("days", "rate")
    def _compute_amount(self):
        for usage in self:
            usage.amount = usage.days * usage.rate

    @api.depends("charge_id.state")
    def _compute_state(self):
        for usage in self:
            usage.state = "charged" if usage.charge_id.state == "posted" else "open"

    def write(self, vals):
        if any(u.state == "charged" for u in self) and set(vals) & {"days", "rate", "project_id", "equipment_id", "date"}:
            raise UserError(_("This usage is already charged to the project; cancel the monthly charge first."))
        return super().write(vals)


class ZcEquipmentCharge(models.Model):
    """Monthly charge of the equipment usage: Dr equipment rent (project) / Cr rent recovered (equipment)."""
    _name = "zc.equipment.charge"
    _description = "Equipment Rent Charge"
    _inherit = ["mail.thread"]
    _order = "date_to desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    usage_ids = fields.One2many("zc.equipment.usage", "charge_id", string="Usage")
    amount_total = fields.Monetary(compute="_compute_total", store=True)
    move_id = fields.Many2one("account.move", readonly=True, copy=False)
    state = fields.Selection([("draft", "Draft"), ("posted", "Posted"), ("cancel", "Cancelled")],
                             default="draft", required=True, tracking=True, copy=False)

    @api.depends("date_from", "date_to")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Equipment rent %(f)s - %(t)s", f=rec.date_from or "", t=rec.date_to or "")

    @api.depends("usage_ids.amount")
    def _compute_total(self):
        for rec in self:
            rec.amount_total = sum(rec.usage_ids.mapped("amount"))

    def action_load(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            usage = self.env["zc.equipment.usage"].search([
                ("company_id", "=", rec.company_id.id), ("date", ">=", rec.date_from), ("date", "<=", rec.date_to),
                "|", ("charge_id", "=", False), ("charge_id.state", "=", "cancel")])
            usage.write({"charge_id": rec.id})
        return True

    def action_post(self):
        for rec in self.filtered(lambda r: r.state == "draft"):
            company = rec.company_id
            if not (company.zc_equipment_cost_account_id and company.zc_equipment_recovery_account_id):
                raise UserError(_("Set the equipment rent accounts in Settings > Contracting first."))
            if not rec.usage_ids:
                rec.action_load()
            missing = rec.usage_ids.filtered(lambda u: not u.project_id.account_id)
            if missing:
                raise UserError(_("Projects without analytic account: %s", ", ".join(missing.project_id.mapped("name"))))
            no_cc = rec.usage_ids.equipment_id.filtered(lambda e: not e.zc_analytic_account_id)
            no_cc.action_zc_create_cost_centre()
            per_project = defaultdict(float)
            per_equipment = defaultdict(float)
            for u in rec.usage_ids:
                per_project[u.project_id] += u.amount
                per_equipment[u.equipment_id] += u.amount
            rounding = rec.currency_id.rounding
            items = []
            for project, amount in per_project.items():
                if float_is_zero(amount, precision_rounding=rounding):
                    continue
                items.append(Command.create({
                    "name": _("Equipment rent - %s", project.name), "account_id": company.zc_equipment_cost_account_id.id,
                    "debit": amount, "credit": 0.0, "analytic_distribution": {str(project.account_id.id): 100}}))
            for equipment, amount in per_equipment.items():
                if float_is_zero(amount, precision_rounding=rounding):
                    continue
                items.append(Command.create({
                    "name": _("Rent recovered - %s", equipment.name),
                    "account_id": company.zc_equipment_recovery_account_id.id, "debit": 0.0, "credit": amount,
                    "analytic_distribution": {str(equipment.zc_analytic_account_id.id): 100}}))
            if not items:
                raise UserError(_("No equipment usage in this period."))
            journal = company.zc_overhead_journal_id or self.env["account.journal"].search(
                [("type", "=", "general"), ("company_id", "=", company.id)], limit=1)
            move = self.env["account.move"].create({"move_type": "entry", "journal_id": journal.id,
                                                    "date": rec.date_to, "ref": rec.name, "line_ids": items})
            move.action_post()
            rec.write({"move_id": move.id, "state": "posted"})
        return True

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.state == "posted"):
            if rec.move_id.state == "posted":
                rec.move_id._reverse_moves([{"date": rec.date_to, "ref": _("Reversal of %s", rec.name)}],
                                           cancel=True)
            rec.state = "cancel"
        return True

    def action_view_move(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.move_id.id,
                "view_mode": "form"}
