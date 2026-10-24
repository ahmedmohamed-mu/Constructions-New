from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    hom_previous_qty = fields.Float(string="Previous Qty", digits="Product Unit", copy=False,
                                    help="Quantity certified before this certificate.")
    hom_cumulative_qty = fields.Float(string="Cumulative Qty", digits="Product Unit", copy=False,
                                      help="Quantity certified to date, including this certificate.")
