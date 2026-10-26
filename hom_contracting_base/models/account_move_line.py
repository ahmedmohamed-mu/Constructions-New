from odoo import api, fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    hom_previous_qty = fields.Float(string="Previous Qty", digits="Product Unit", copy=False,
                                    help="Quantity certified before this certificate.")
    hom_cumulative_qty = fields.Float(string="Cumulative Qty", digits="Product Unit", copy=False,
                                      help="Quantity certified to date, including this certificate.")

    # Advances and retentions are balance-sheet items of the contract: they must never carry a project
    # analytic, otherwise the project cost / revenue (and its margin) would include them.
    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._hom_strip_contract_analytics()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get("hom_strip_done") and ("analytic_distribution" in vals or "account_id" in vals):
            self._hom_strip_contract_analytics()
        return res

    def _hom_strip_contract_analytics(self):
        cache = {}
        to_strip = self.browse()
        for line in self.filtered("analytic_distribution"):
            company = line.company_id or self.env.company
            if company.id not in cache:
                cache[company.id] = company._hom_contract_balance_accounts()
            if line.account_id in cache[company.id]:
                to_strip |= line
        if to_strip:
            to_strip.with_context(hom_strip_done=True).write({"analytic_distribution": False})


class AccountMove(models.Model):
    _inherit = "account.move"

    hom_retention_order_id = fields.Many2one("sale.order", string="Retention Release of", readonly=True,
                                             copy=False, index="btree_not_null")
    hom_retention_purchase_id = fields.Many2one("purchase.order", string="Retention Release of Subcontract",
                                                readonly=True, copy=False, index="btree_not_null")
