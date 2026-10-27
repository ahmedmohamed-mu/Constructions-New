from odoo import api, fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    zc_charge_partner_id = fields.Many2one(
        "res.partner", string="Charge to subcontractor",
        help="Material issued from our stores to a subcontractor whose subcontract includes the material: "
             "its cost is deducted from the subcontractor's next certificate.")
    zc_charge_deduction_id = fields.Many2one("zc.certificate.deduction", string="Charged in", readonly=True,
                                             copy=False, index="btree_not_null")
    zc_charge_amount = fields.Monetary(string="Material cost", compute="_compute_zc_charge_amount",
                                       currency_field="zc_company_currency_id")
    zc_company_currency_id = fields.Many2one(related="company_id.currency_id")

    @api.depends("move_ids.value", "move_ids.quantity", "state")
    def _compute_zc_charge_amount(self):
        for picking in self:
            total = 0.0
            for move in picking.move_ids.filtered(lambda m: m.state == "done"):
                total += abs(move.value) if move.value else move.quantity * move.product_id.standard_price
            picking.zc_charge_amount = total
