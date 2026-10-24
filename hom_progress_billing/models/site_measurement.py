from odoo import _, api, fields, models
from odoo.exceptions import UserError


class HomSiteMeasurement(models.Model):
    """Quantity executed on site for a BOQ item, recorded daily by the site engineer.

    Open measurements are pulled automatically into the next progress certificate.
    """
    _name = "hom.site.measurement"
    _description = "Site Measurement"
    _order = "date desc, id desc"
    _check_company_auto = True

    date = fields.Date(default=fields.Date.context_today, required=True, index=True)
    sale_line_id = fields.Many2one(
        "sale.order.line", string="BOQ Item", required=True, index=True,
        domain="[('order_id.state', '=', 'sale'), ('order_id.hom_project_type', '=', 'contracting'),"
               " ('display_type', '=', False), ('is_downpayment', '=', False)]")
    sale_order_id = fields.Many2one(related="sale_line_id.order_id", store=True, string="Contract")
    company_id = fields.Many2one(related="sale_line_id.company_id", store=True)
    project_id = fields.Many2one("project.project", compute="_compute_project_id", store=True)
    product_id = fields.Many2one(related="sale_line_id.product_id")
    product_uom_id = fields.Many2one(related="sale_line_id.product_uom_id", string="Unit")
    quantity = fields.Float(string="Executed Qty", digits="Product Unit", required=True)
    location = fields.Char(string="Location / Zone", help="e.g. Ground floor - zone B")
    user_id = fields.Many2one("res.users", string="Site Engineer", default=lambda self: self.env.user)
    note = fields.Char()
    certificate_id = fields.Many2one("hom.progress.certificate", string="Certificate", readonly=True,
                                     copy=False, index="btree_not_null", ondelete="set null")
    state = fields.Selection([("open", "Not certified"), ("in_certificate", "In certificate"),
                              ("certified", "Certified")], compute="_compute_state", store=True)

    @api.depends("sale_order_id")
    def _compute_project_id(self):
        for rec in self:
            rec.project_id = rec.sale_order_id.project_ids[:1]

    @api.depends("certificate_id", "certificate_id.state")
    def _compute_state(self):
        for rec in self:
            if not rec.certificate_id or rec.certificate_id.state == "cancel":
                rec.state = "open"
            elif rec.certificate_id.state in ("approved", "invoiced"):
                rec.state = "certified"
            else:
                rec.state = "in_certificate"

    def write(self, vals):
        if set(vals) - {"certificate_id"} and self.filtered(lambda m: m.state == "certified"):
            raise UserError(_("Certified measurements cannot be modified."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda m: m.state == "certified"):
            raise UserError(_("Certified measurements cannot be deleted."))
        return super().unlink()
