from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    zc_wir_required = fields.Boolean(
        string="Inspection before certification",
        help="Site measurements enter an owner or subcontractor certificate only after the consultant has "
             "approved their work inspection request (WIR).")
    zc_wir_review_days = fields.Integer(string="WIR answer within (days)", default=2)
    zc_consultant_review_days = fields.Integer(string="Consultant certifies within (days)", default=14)
    zc_sub_multi_approval = fields.Boolean(
        string="Subcontractor approval chain",
        help="Subcontractor certificates are checked by the technical office (quantity surveyor) and approved by "
             "the project manager before they can be billed.")
    zc_qs_user_id = fields.Many2one("res.users", string="Technical office (QS)")
    zc_settlement_journal_id = fields.Many2one("account.journal", string="Withholding settlement journal",
                                               domain="[('type', '=', 'general')]")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    zc_wir_required = fields.Boolean(related="company_id.zc_wir_required", readonly=False)
    zc_wir_review_days = fields.Integer(related="company_id.zc_wir_review_days", readonly=False)
    zc_consultant_review_days = fields.Integer(related="company_id.zc_consultant_review_days", readonly=False)
    zc_sub_multi_approval = fields.Boolean(related="company_id.zc_sub_multi_approval", readonly=False)
    zc_qs_user_id = fields.Many2one(related="company_id.zc_qs_user_id", readonly=False)
    zc_settlement_journal_id = fields.Many2one(related="company_id.zc_settlement_journal_id", readonly=False)
