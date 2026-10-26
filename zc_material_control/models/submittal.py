from odoo import _, api, fields, models
from odoo.tools import float_compare


class HomSubmittal(models.Model):
    _inherit = "hom.submittal"

    zc_sale_line_id = fields.Many2one(
        "sale.order.line", string="BOQ Item",
        domain="[('order_id', '=', sale_order_id), ('display_type', '=', False), ('is_downpayment', '=', False)]",
        help="The BOQ item this shop drawing details. On approval (A/B) its quantity becomes the execution "
             "quantity that purchasing is checked against.")
    zc_sd_qty = fields.Float(string="Shop Drawing Qty", digits="Product Unit", tracking=True)
    zc_contract_qty = fields.Float(related="zc_sale_line_id.product_uom_qty", string="BOQ Qty")
    zc_qty_diff = fields.Float(string="Difference vs BOQ", compute="_compute_zc_qty_diff", digits="Product Unit")

    @api.depends("zc_sd_qty", "zc_contract_qty")
    def _compute_zc_qty_diff(self):
        for sub in self:
            sub.zc_qty_diff = (sub.zc_sd_qty - sub.zc_contract_qty) if sub.zc_sale_line_id and sub.zc_sd_qty else 0.0

    def write(self, vals):
        res = super().write(vals)
        if vals.get("state") == "approved":
            self._zc_apply_shop_drawing_qty()
        return res

    def _zc_apply_shop_drawing_qty(self):
        for sub in self.filtered(lambda s: s.zc_sale_line_id and s.zc_sd_qty > 0 and s.state == "approved"):
            line = sub.zc_sale_line_id.sudo()
            line.write({"zc_sd_qty": sub.zc_sd_qty, "zc_sd_submittal_id": sub.id})
            body = _("Shop drawing %(sd)s approved: execution quantity of %(item)s is now %(q)s %(u)s (BOQ %(b)s).",
                     sd=sub.name, item=line.product_id.display_name, q=sub.zc_sd_qty,
                     u=line.product_uom_id.name, b=line.product_uom_qty)
            sub.message_post(body=body)
            if sub.project_id:
                sub.project_id.sudo().message_post(body=body)
            if float_compare(sub.zc_sd_qty, line.product_uom_qty, precision_digits=6) > 0:
                # the owner pays the BOQ quantity only: an increase needs a CRVO with the consultant
                responsible = sub.responsible_id or sub.project_id.user_id
                if responsible:
                    sub.activity_schedule(
                        "mail.mail_activity_data_todo", user_id=responsible.id,
                        summary=_("Shop drawing above BOQ: %s", line.product_id.display_name),
                        note=_("The approved shop drawing (%(q)s) exceeds the BOQ quantity (%(b)s). The owner pays the "
                               "BOQ quantity only: prepare a change request (CRVO) for the difference.",
                               q=sub.zc_sd_qty, b=line.product_uom_qty))
