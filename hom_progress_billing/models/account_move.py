from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    hom_certificate_id = fields.Many2one("hom.progress.certificate", string="Progress Certificate",
                                         readonly=True, copy=False, index="btree_not_null")


class ResCompany(models.Model):
    _inherit = "res.company"

    hom_certificate_accountant_id = fields.Many2one(
        "res.users", string="Certificates Accountant",
        help="Receives a to-do when a progress certificate is approved.")
    hom_certificate_day = fields.Integer(string="Monthly Certificate Day", default=25,
                                         help="Day of the month on which draft certificates are prepared.")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    hom_certificate_accountant_id = fields.Many2one(related="company_id.hom_certificate_accountant_id", readonly=False)
    hom_certificate_day = fields.Integer(related="company_id.hom_certificate_day", readonly=False)
