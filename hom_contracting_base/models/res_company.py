from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    hom_retention_owner_product_id = fields.Many2one(
        "product.product", string="Owner Retention Product",
        help="Negative line added to owner certificates; its income account should be the "
             "retention receivable (balance sheet), e.g. 102020.")
    hom_retention_sub_product_id = fields.Many2one(
        "product.product", string="Subcontractor Retention Product",
        help="Negative line added to subcontractor certificates; its expense account should be "
             "the retention payable (balance sheet), e.g. 201031.")
    hom_sub_advance_product_id = fields.Many2one(
        "product.product", string="Subcontractor Advance Product",
        help="Used for the subcontractor advance bill; account = advances to subcontractors, e.g. 102021.")
    hom_sub_advance_recovery_product_id = fields.Many2one(
        "product.product", string="Subcontractor Advance Recovery Product",
        help="Negative line on subcontractor certificates recovering the advance (same account as the advance).")
    hom_auto_draft_invoice = fields.Boolean(
        string="Draft invoices from approved certificates",
        help="When a progress certificate (owner or subcontractor) is approved, its invoice / vendor bill is "
             "prepared automatically as a draft and the accountant only reviews and posts it.")
    hom_management_user_id = fields.Many2one(
        "res.users", string="Top Management",
        help="Receives contracting alerts (budget overrun, margin risk), usually the CEO.")

    def _hom_contract_balance_accounts(self):
        """Advance and retention accounts of the contracts (customer and subcontractor side)."""
        self.ensure_one()
        accounts = self.env["account.account"]
        if "downpayment_account_id" in self._fields:
            accounts |= self.downpayment_account_id
        products = (self.hom_retention_owner_product_id | self.hom_retention_sub_product_id
                    | self.hom_sub_advance_product_id | self.hom_sub_advance_recovery_product_id)
        for product in products:
            found = product.product_tmpl_id.with_company(self)._get_product_accounts()
            accounts |= (found.get("income") or accounts) | (found.get("expense") or accounts)
        return accounts
