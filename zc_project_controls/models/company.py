from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    zc_overhead_account_id = fields.Many2one(
        "account.account", string="Allocated Overhead (project cost)",
        help="Debited on each project (with its analytic account) when G&A overhead is allocated.")
    zc_overhead_absorbed_account_id = fields.Many2one(
        "account.account", string="Overhead Absorbed (contra G&A)",
        help="Credited by the allocation: G&A expenses stay in the books, part of them now sits on the projects.")
    zc_overhead_basis = fields.Selection(
        [("revenue", "Revenue of the period"), ("direct_cost", "Direct cost of the period"), ("manual", "Manual %")],
        string="Overhead Allocation Basis", default="revenue")
    zc_equipment_cost_account_id = fields.Many2one(
        "account.account", string="Equipment Rent (project cost)",
        help="Debited on the project for the internal daily rent of company equipment.")
    zc_equipment_recovery_account_id = fields.Many2one(
        "account.account", string="Equipment Rent Recovered",
        help="Credited on the equipment cost centre: compared with the real cost of the machine "
             "(fuel, repairs, depreciation) it shows whether the internal rate covers the machine.")
    zc_labour_product_id = fields.Many2one(
        "product.product", string="Daily Labour Service", domain=[("type", "=", "service")])
    zc_labour_partner_id = fields.Many2one(
        "res.partner", string="Daily Labour Payee",
        help="Partner of the weekly labour bills (e.g. the site foreman who pays the workers in cash).")
    zc_overhead_journal_id = fields.Many2one(
        "account.journal", string="Project Controls Journal", domain=[("type", "=", "general")])
    zc_vo_owner_agreement = fields.Boolean(
        string="Variations need the owner's agreed rate",
        help="New variation orders follow the owner cycle (instruction, impact, negotiation, agreed rate): "
             "items are executed as provisional and certified only once the rate is agreed.")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    zc_overhead_account_id = fields.Many2one(related="company_id.zc_overhead_account_id", readonly=False)
    zc_overhead_absorbed_account_id = fields.Many2one(related="company_id.zc_overhead_absorbed_account_id",
                                                      readonly=False)
    zc_overhead_basis = fields.Selection(related="company_id.zc_overhead_basis", readonly=False)
    zc_equipment_cost_account_id = fields.Many2one(related="company_id.zc_equipment_cost_account_id", readonly=False)
    zc_equipment_recovery_account_id = fields.Many2one(related="company_id.zc_equipment_recovery_account_id",
                                                       readonly=False)
    zc_labour_product_id = fields.Many2one(related="company_id.zc_labour_product_id", readonly=False)
    zc_labour_partner_id = fields.Many2one(related="company_id.zc_labour_partner_id", readonly=False)
    zc_overhead_journal_id = fields.Many2one(related="company_id.zc_overhead_journal_id", readonly=False)
    zc_vo_owner_agreement = fields.Boolean(related="company_id.zc_vo_owner_agreement", readonly=False)
