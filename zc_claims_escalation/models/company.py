from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    zc_claim_notice_days = fields.Integer(string="Claim notice within (days)", default=28,
                                          help="FIDIC 20.2.1: notice of claim within 28 days of becoming aware.")
    zc_claim_detail_days = fields.Integer(string="Detailed claim within (days)", default=84,
                                          help="FIDIC 20.2.4: fully detailed claim within 84 days of becoming aware.")
    zc_claim_answer_days = fields.Integer(string="Engineer answers within (days)", default=42,
                                          help="FIDIC 3.7.3: agreement / determination within 42 days.")
    zc_claim_nod_days = fields.Integer(string="Notice of dissatisfaction within (days)", default=28,
                                       help="FIDIC 3.7.5: otherwise the determination is final and binding.")
    zc_escalation_product_id = fields.Many2one("product.product", string="Price escalation product")
    zc_claim_product_id = fields.Many2one("product.product", string="Claim settlement product")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    zc_claim_notice_days = fields.Integer(related="company_id.zc_claim_notice_days", readonly=False)
    zc_claim_detail_days = fields.Integer(related="company_id.zc_claim_detail_days", readonly=False)
    zc_claim_answer_days = fields.Integer(related="company_id.zc_claim_answer_days", readonly=False)
    zc_claim_nod_days = fields.Integer(related="company_id.zc_claim_nod_days", readonly=False)
