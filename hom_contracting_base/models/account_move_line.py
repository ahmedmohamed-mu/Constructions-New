from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    hom_previous_qty = fields.Float(string="Previous Qty", digits="Product Unit", copy=False,
                                    help="Quantity certified before this certificate.")
    hom_cumulative_qty = fields.Float(string="Cumulative Qty", digits="Product Unit", copy=False,
                                      help="Quantity certified to date, including this certificate.")


class AccountMove(models.Model):
    _inherit = "account.move"

    hom_retention_order_id = fields.Many2one("sale.order", string="Retention Release of", readonly=True,
                                             copy=False, index="btree_not_null")
    hom_retention_purchase_id = fields.Many2one("purchase.order", string="Retention Release of Subcontract",
                                                readonly=True, copy=False, index="btree_not_null")
