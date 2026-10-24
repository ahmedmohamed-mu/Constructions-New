from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    hom_is_subcontract = fields.Boolean(string="Subcontract", tracking=True)
    hom_advance_amount = fields.Monetary(string="Advance Amount", currency_field="currency_id", tracking=True)
    hom_advance_recovery_pct = fields.Float(string="Advance Recovery %", default=10.0)
    hom_retention_pct = fields.Float(string="Retention %", default=5.0)
