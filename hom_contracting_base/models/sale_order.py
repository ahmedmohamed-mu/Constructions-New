from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


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
    hom_retention_release_ids = fields.One2many("account.move", "hom_retention_order_id", string="Retention Releases")
    hom_retention_held = fields.Monetary(string="Retention Held", compute="_compute_hom_retention")
    hom_retention_released = fields.Monetary(string="Retention Released", compute="_compute_hom_retention")
    hom_retention_balance = fields.Monetary(string="Retention to Collect", compute="_compute_hom_retention")

    @api.onchange("hom_project_type")
    def _onchange_hom_project_type(self):
        """FM contracts are monthly service invoices: no advance, no retention, no consultant."""
        for order in self:
            if order.hom_project_type == "fm":
                order.hom_advance_pct = 0.0
                order.hom_retention_pct = 0.0
                order.hom_consultant_id = False
            elif not order.hom_advance_pct and not order.hom_retention_pct:
                order.hom_advance_pct = 25.0
                order.hom_retention_pct = 5.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # a recurring (subscription) order is a maintenance contract unless stated otherwise
            if vals.get("plan_id") and "hom_project_type" not in vals:
                vals["hom_project_type"] = "fm"
            if vals.get("hom_project_type") == "fm":
                vals.setdefault("hom_advance_pct", 0.0)
                vals.setdefault("hom_retention_pct", 0.0)
        return super().create(vals_list)

    def _compute_hom_retention(self):
        for order in self:
            product = order.company_id.hom_retention_owner_product_id
            held = released = 0.0
            if product:
                for move in order.invoice_ids.filtered(lambda m: m.state == "posted"):
                    sign = -1 if move.move_type == "out_refund" else 1
                    held -= sign * sum(move.invoice_line_ids.filtered(lambda l: l.product_id == product).mapped("price_subtotal"))
                for move in order.hom_retention_release_ids.filtered(lambda m: m.state == "posted"):
                    released += sum(move.invoice_line_ids.filtered(lambda l: l.product_id == product).mapped("price_subtotal"))
            order.hom_retention_held = held
            order.hom_retention_released = released
            order.hom_retention_balance = held - released

    def action_hom_release_retention(self):
        self.ensure_one()
        product = self.company_id.hom_retention_owner_product_id
        amount = self.hom_retention_balance
        if not product or amount <= 0:
            raise UserError(_("There is no retention left to release on this contract."))
        if self.hom_retention_release_ids.filtered(lambda m: m.state == "draft"):
            raise UserError(_("A draft retention release invoice already exists."))
        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner_invoice_id.id,
            "currency_id": self.currency_id.id,
            "invoice_origin": self.name,
            "ref": _("Retention release - %s", self.name),
            "hom_retention_order_id": self.id,
            "invoice_line_ids": [Command.create({
                "product_id": product.id,
                "name": _("Release of retention - %s", self.client_order_ref or self.name),
                "quantity": 1,
                "price_unit": amount,
                "tax_ids": [Command.clear()],
            })],
        })
        self.message_post(body=_("Retention release invoice %s prepared.", move.display_name))
        return move._get_records_action()
