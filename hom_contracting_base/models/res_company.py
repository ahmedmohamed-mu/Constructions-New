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
