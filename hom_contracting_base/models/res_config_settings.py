from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    hom_retention_owner_product_id = fields.Many2one(related="company_id.hom_retention_owner_product_id", readonly=False)
    hom_retention_sub_product_id = fields.Many2one(related="company_id.hom_retention_sub_product_id", readonly=False)
    hom_sub_advance_product_id = fields.Many2one(related="company_id.hom_sub_advance_product_id", readonly=False)
    hom_sub_advance_recovery_product_id = fields.Many2one(related="company_id.hom_sub_advance_recovery_product_id", readonly=False)
