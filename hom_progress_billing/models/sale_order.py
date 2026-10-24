from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    hom_certificate_ids = fields.One2many("hom.progress.certificate", "sale_order_id", string="Progress Certificates")
    hom_certificate_count = fields.Integer(compute="_compute_hom_certificate_count")

    def _compute_hom_certificate_count(self):
        for order in self:
            order.hom_certificate_count = len(order.hom_certificate_ids)

    def action_hom_certificates(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_progress_billing.action_hom_progress_certificate")
        action["domain"] = [("sale_order_id", "=", self.id)]
        action["context"] = {"default_sale_order_id": self.id}
        return action
