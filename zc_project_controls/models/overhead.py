from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_is_zero, float_round

from .tools import analytic_amount


class ZcOverheadAllocation(models.Model):
    """Monthly share of the head-office expenses (G&A) charged to the running projects.

    A real journal entry moves the amount: Dr allocated overhead (project analytic) / Cr overhead absorbed.
    The P&L total does not change; each project's margin now carries its share, and the project dossier
    still ties to the general ledger.
    """
    _name = "zc.overhead.allocation"
    _description = "G&A Overhead Allocation"
    _inherit = ["mail.thread"]
    _order = "date_to desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    basis = fields.Selection([("revenue", "Revenue of the period"), ("direct_cost", "Direct cost of the period"),
                              ("manual", "Manual %")], required=True,
                             default=lambda self: self.env.company.zc_overhead_basis or "revenue")
    pool_account_ids = fields.Many2many("account.account", string="G&A Accounts",
                                        help="Head-office expense accounts whose balance is allocated.")
    pool_amount = fields.Monetary(string="G&A of the Period", compute="_compute_pool")
    amount = fields.Monetary(string="Amount to Allocate", required=True, tracking=True)
    line_ids = fields.One2many("zc.overhead.allocation.line", "allocation_id", copy=False)
    allocated_total = fields.Monetary(compute="_compute_allocated")
    move_id = fields.Many2one("account.move", string="Journal Entry", readonly=True, copy=False)
    state = fields.Selection([("draft", "Draft"), ("posted", "Posted"), ("cancel", "Cancelled")],
                             default="draft", required=True, tracking=True, copy=False)

    @api.depends("date_from", "date_to")
    def _compute_name(self):
        for rec in self:
            rec.name = _("Overhead %(f)s - %(t)s", f=rec.date_from or "", t=rec.date_to or "")

    @api.depends("pool_account_ids", "date_from", "date_to")
    def _compute_pool(self):
        for rec in self:
            if not (rec.pool_account_ids and rec.date_from and rec.date_to):
                rec.pool_amount = 0.0
                continue
            lines = self.env["account.move.line"].sudo().search([
                ("account_id", "in", rec.pool_account_ids.ids), ("parent_state", "=", "posted"),
                ("date", ">=", rec.date_from), ("date", "<=", rec.date_to), ("company_id", "=", rec.company_id.id)])
            rec.pool_amount = sum(lines.mapped("balance"))

    @api.depends("line_ids.amount")
    def _compute_allocated(self):
        for rec in self:
            rec.allocated_total = sum(rec.line_ids.mapped("amount"))

    @api.onchange("pool_account_ids", "date_from", "date_to")
    def _onchange_pool(self):
        self._compute_pool()
        if self.pool_amount and not self.amount:
            self.amount = self.pool_amount

    @api.model
    def _running_projects(self, company):
        orders = self.env["sale.order"].sudo().search([("state", "=", "sale"), ("company_id", "=", company.id),
                                                       ("hom_project_type", "=", "contracting")])
        return orders.project_ids.filtered("account_id")

    def action_compute(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only a draft allocation can be recomputed."))
            if not rec.amount and rec.pool_amount:
                rec.amount = rec.pool_amount
            exclude = tuple(a.id for a in (rec.company_id.zc_overhead_account_id,) if a)
            manual = {l.project_id.id: l.manual_pct for l in rec.line_ids}
            rec.line_ids.unlink()
            vals = []
            for project in self._running_projects(rec.company_id):
                if rec.basis == "revenue":
                    base = analytic_amount(self.env, project.account_id, rec.date_to, rec.date_from, "revenue")
                elif rec.basis == "direct_cost":
                    base = analytic_amount(self.env, project.account_id, rec.date_to, rec.date_from, "cost",
                                           exclude_general_accounts=exclude)
                else:
                    base = manual.get(project.id, 0.0)
                if base > 0 or rec.basis == "manual":
                    vals.append({"allocation_id": rec.id, "project_id": project.id, "basis_value": base,
                                 "manual_pct": manual.get(project.id, 0.0)})
            lines = self.env["zc.overhead.allocation.line"].create(vals)
            rec._spread(lines)
        return True

    def _spread(self, lines):
        """Ratios on the basis; amounts rounded to the currency with the rounding left on the largest line."""
        self.ensure_one()
        total = sum(lines.mapped("basis_value"))
        if not total:
            return
        digits = self.currency_id.decimal_places
        for line in lines:
            line.ratio = line.basis_value / total * 100.0
            line.amount = float_round(self.amount * line.basis_value / total, precision_digits=digits)
        diff = float_round(self.amount - sum(lines.mapped("amount")), precision_digits=digits)
        if diff and lines:
            biggest = lines.sorted("amount")[-1]
            biggest.amount += diff

    def action_post(self):
        for rec in self:
            if rec.state != "draft":
                continue
            company = rec.company_id
            if not (company.zc_overhead_account_id and company.zc_overhead_absorbed_account_id):
                raise UserError(_("Set the overhead accounts in Settings > Contracting first."))
            if not rec.line_ids:
                rec.action_compute()
            lines = rec.line_ids.filtered(lambda l: not float_is_zero(l.amount, precision_rounding=rec.currency_id.rounding))
            if not lines:
                raise UserError(_("Nothing to allocate: no project has a basis in this period."))
            journal = company.zc_overhead_journal_id or self.env["account.journal"].search(
                [("type", "=", "general"), ("company_id", "=", company.id)], limit=1)
            items = [Command.create({
                "name": _("G&A overhead %(p)s (%(r)s%%)", p=l.project_id.name, r=round(l.ratio, 2)),
                "account_id": company.zc_overhead_account_id.id, "debit": l.amount, "credit": 0.0,
                "analytic_distribution": {str(l.project_id.account_id.id): 100},
            }) for l in lines]
            items.append(Command.create({
                "name": rec.name, "account_id": company.zc_overhead_absorbed_account_id.id,
                "debit": 0.0, "credit": sum(lines.mapped("amount")),
            }))
            move = self.env["account.move"].create({
                "move_type": "entry", "journal_id": journal.id, "date": rec.date_to, "ref": rec.name,
                "line_ids": items,
            })
            move.action_post()
            rec.write({"move_id": move.id, "state": "posted"})
            rec.message_post(body=_("Posted %s.", move.display_name))
        return True

    def action_cancel(self):
        for rec in self.filtered(lambda r: r.state == "posted"):
            if rec.move_id and rec.move_id.state == "posted":
                rec.move_id._reverse_moves([{"date": rec.date_to, "ref": _("Reversal of %s", rec.name)}],
                                           cancel=True)
            rec.state = "cancel"
        return True

    def action_view_move(self):
        self.ensure_one()
        return {"type": "ir.actions.act_window", "res_model": "account.move", "res_id": self.move_id.id,
                "view_mode": "form"}


class ZcOverheadAllocationLine(models.Model):
    _name = "zc.overhead.allocation.line"
    _description = "G&A Overhead Allocation - Project"
    _order = "amount desc"

    allocation_id = fields.Many2one("zc.overhead.allocation", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="allocation_id.currency_id")
    project_id = fields.Many2one("project.project", required=True)
    basis_value = fields.Monetary(string="Basis")
    manual_pct = fields.Float(string="Manual %")
    ratio = fields.Float(string="Share %", digits=(16, 2))
    amount = fields.Monetary()
