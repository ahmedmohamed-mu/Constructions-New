from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ZcQuantityDispute(models.Model):
    """Quantity submitted to the consultant but not certified: money the contractor still claims."""
    _name = "zc.quantity.dispute"
    _description = "Quantity Claim (cut by the consultant)"
    _inherit = ["mail.thread"]
    _order = "id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    state = fields.Selection([
        ("open", "Open - to claim"),
        ("resubmitted", "Resubmitted"),
        ("closed", "Closed"),
        ("waived", "Accepted (waived)"),
    ], default="open", required=True, tracking=True)
    certificate_id = fields.Many2one("hom.progress.certificate", string="Cut in certificate", required=True,
                                     ondelete="cascade", index=True)
    sale_order_id = fields.Many2one(related="certificate_id.sale_order_id", store=True)
    project_id = fields.Many2one(related="certificate_id.project_id", store=True)
    partner_id = fields.Many2one(related="certificate_id.partner_id", store=True, string="Owner")
    company_id = fields.Many2one(related="certificate_id.company_id", store=True)
    currency_id = fields.Many2one(related="certificate_id.currency_id", store=True)
    sale_line_id = fields.Many2one("sale.order.line", string="BOQ Item", required=True)
    product_id = fields.Many2one(related="sale_line_id.product_id")
    product_uom_id = fields.Many2one(related="sale_line_id.product_uom_id", string="Unit")
    qty = fields.Float(string="Qty cut", digits="Product Unit", required=True)
    price_unit = fields.Float(digits="Product Price")
    amount = fields.Monetary(compute="_compute_amount", store=True)
    reason = fields.Char(string="Consultant's reason")
    resubmit_cert_id = fields.Many2one("hom.progress.certificate", string="Resubmitted in", readonly=True)
    recovered_qty = fields.Float(digits="Product Unit", readonly=True)
    recovered_amount = fields.Monetary(compute="_compute_amount", store=True)

    @api.depends("qty", "price_unit", "recovered_qty")
    def _compute_amount(self):
        for rec in self:
            rec.amount = rec.qty * rec.price_unit
            rec.recovered_amount = rec.recovered_qty * rec.price_unit

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.quantity.dispute") or _("New")
        return super().create(vals_list)

    def action_waive(self):
        if self.filtered(lambda d: d.state not in ("open",)):
            raise UserError(_("Only open claims can be waived."))
        self.write({"state": "waived"})

    def action_reopen(self):
        self.filtered(lambda d: d.state == "waived").write({"state": "open"})
