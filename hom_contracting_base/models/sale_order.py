from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    hom_project_type = fields.Selection(
        [("contracting", "Contracting / مقاولات"), ("fm", "Facility Management / صيانة")],
        string="Project Type", default="contracting", tracking=True)
    hom_consultant_id = fields.Many2one("res.partner", string="Consultant", tracking=True)
    hom_advance_pct = fields.Float(string="Advance %", default=25.0, tracking=True,
                                   help="Advance payment recovered pro-rata from each progress certificate.")
    hom_retention_pct = fields.Float(string="Retention %", default=5.0, tracking=True,
                                     help="Retention withheld from each progress certificate.")
