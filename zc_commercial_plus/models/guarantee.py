from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.zc_project_controls.hooks import _account

MARGIN_ACCOUNT = ("102030", "asset_current", "Cash margins on letters of guarantee", "هوامش نقدية لخطابات الضمان")
COMMISSION_ACCOUNT = ("400019", "expense_direct_cost", "Letters of guarantee commission - projects",
                      "عمولات خطابات الضمان - مشروعات")


class HomBankGuarantee(models.Model):
    """The register becomes accounting for the guarantees we issue: the cash margin the bank blocks is an asset
    taken from the bank account until the release, the bank commission is a cost of the project."""
    _inherit = "hom.bank.guarantee"

    zc_journal_id = fields.Many2one("account.journal", string="Bank journal", check_company=True,
                                    domain="[('type', '=', 'bank')]",
                                    default=lambda self: self.env["account.journal"].search(
                                        [("type", "=", "bank"), ("company_id", "=", self.env.company.id)], limit=1))
    zc_commission_amount = fields.Monetary(string="Commission for the period", compute="_compute_zc_commission",
                                           store=True, readonly=False,
                                           help="Original amount x yearly commission % x months covered / 12.")
    zc_bank_move_id = fields.Many2one("account.move", string="Bank entry", readonly=True, copy=False)
    zc_release_move_id = fields.Many2one("account.move", string="Margin returned", readonly=True, copy=False)

    @api.depends("amount", "commission_pct", "issue_date", "expiry_date")
    def _compute_zc_commission(self):
        for g in self:
            months = 0.0
            if g.issue_date and g.expiry_date and g.expiry_date > g.issue_date:
                months = (g.expiry_date - g.issue_date).days / 30.4375
            g.zc_commission_amount = g.currency_id.round(g.amount * (g.commission_pct or 0.0) / 100.0 * months / 12.0) \
                if g.currency_id else 0.0

    def _zc_accounts(self):
        env = self.env(su=True)
        company = self.company_id
        return _account(env, company, *MARGIN_ACCOUNT), _account(env, company, *COMMISSION_ACCOUNT)

    def action_zc_post_bank_entry(self):
        for g in self:
            if g.direction != "issued":
                raise UserError(_("Only the guarantees we issue are booked: a guarantee received is not our money."))
            if g.zc_bank_move_id and g.zc_bank_move_id.state != "cancel":
                raise UserError(_("Already booked: %s", g.zc_bank_move_id.name))
            journal = g.zc_journal_id
            if not journal or not journal.default_account_id:
                raise UserError(_("Choose the bank journal of the guarantee."))
            if not g.cash_margin and not g.zc_commission_amount:
                raise UserError(_("Nothing to book: no cash margin and no commission."))
            margin_acc, commission_acc = g._zc_accounts()
            bank = journal.default_account_id
            label = "%s - %s" % (g.name, g.bank_name or journal.name)
            lines = []
            if g.cash_margin:
                lines += [Command.create({"account_id": margin_acc.id, "name": _("Cash margin %s", label),
                                          "debit": g.cash_margin, "partner_id": g.partner_id.id}),
                          Command.create({"account_id": bank.id, "name": _("Cash margin %s", label),
                                          "credit": g.cash_margin})]
            if g.zc_commission_amount:
                commission = {"account_id": commission_acc.id, "name": _("Bank commission %s", label),
                              "debit": g.zc_commission_amount}
                project = g.sale_order_id.project_ids[:1]
                if project.account_id:
                    commission["analytic_distribution"] = {str(project.account_id.id): 100}
                lines += [Command.create(commission),
                          Command.create({"account_id": bank.id, "name": _("Bank commission %s", label),
                                          "credit": g.zc_commission_amount})]
            move = self.env["account.move"].create({"journal_id": journal.id, "date": g.issue_date,
                                                    "ref": _("Letter of guarantee %s", g.name), "line_ids": lines})
            move.action_post()
            g.zc_bank_move_id = move
            g.message_post(body=_("Booked: cash margin %(m)s, commission %(c)s (%(e)s).", m=g.cash_margin,
                                  c=g.zc_commission_amount, e=move.name))
        return True

    def action_release(self):
        res = super().action_release()
        for g in self.filtered(lambda x: x.zc_bank_move_id.state == "posted" and x.cash_margin
                               and not x.zc_release_move_id):
            margin_acc, _commission_acc = g._zc_accounts()
            bank = g.zc_journal_id.default_account_id
            move = self.env["account.move"].create({
                "journal_id": g.zc_journal_id.id, "date": fields.Date.context_today(self),
                "ref": _("Letter of guarantee %s released", g.name),
                "line_ids": [Command.create({"account_id": bank.id, "name": _("Cash margin returned %s", g.name),
                                             "debit": g.cash_margin}),
                             Command.create({"account_id": margin_acc.id, "partner_id": g.partner_id.id,
                                             "name": _("Cash margin returned %s", g.name), "credit": g.cash_margin})]})
            move.action_post()
            g.zc_release_move_id = move
        return res


# ---------------------------------------------------------------------------
# Deductions applied to every new contract / subcontract (withholding tax)
# ---------------------------------------------------------------------------
class ZcDeductionType(models.Model):
    _inherit = "zc.deduction.type"

    zc_auto_apply = fields.Boolean(string="On every new contract",
                                   help="Added by itself to every new owner contract (owner deductions) or "
                                        "subcontract (subcontractor deductions), e.g. the withholding tax.")


class SaleOrder(models.Model):
    _inherit = "sale.order"

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order in orders.filtered(lambda o: o.hom_project_type == "contracting" and not o.zc_deduction_type_ids):
            types = self.env["zc.deduction.type"].search([("scope", "=", "owner"), ("zc_auto_apply", "=", True),
                                                          ("company_id", "=", order.company_id.id)])
            if types:
                order.zc_deduction_type_ids = types
        return orders


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def _zc_auto_deductions(self):
        for order in self.filtered(lambda o: o.hom_is_subcontract and not o.zc_deduction_type_ids):
            types = self.env["zc.deduction.type"].search([("scope", "=", "subcontract"), ("zc_auto_apply", "=", True),
                                                          ("company_id", "=", order.company_id.id)])
            if types:
                order.zc_deduction_type_ids = types

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        orders._zc_auto_deductions()
        return orders

    def write(self, vals):
        res = super().write(vals)
        if vals.get("hom_is_subcontract"):
            self._zc_auto_deductions()
        return res
