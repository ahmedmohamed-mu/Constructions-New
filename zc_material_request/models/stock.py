from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = "stock.picking"

    zc_qc_state = fields.Selection([("pending", "Waiting technical acceptance"), ("accepted", "Accepted"),
                                    ("rejected", "Rejected")], string="Technical acceptance", copy=False,
                                   tracking=True)
    zc_qc_note = fields.Text(string="Inspection notes", copy=False)
    zc_qc_user_id = fields.Many2one("res.users", string="Inspected by", copy=False, readonly=True)
    zc_qc_date = fields.Date(string="Inspected on", copy=False, readonly=True)

    def _action_done(self):
        res = super()._action_done()
        # material arriving at a project site store waits for the engineer's / QC acceptance
        site_stores = self.env["project.project"].sudo().search([("zc_warehouse_id", "!=", False)]).zc_warehouse_id
        for picking in self.filtered(lambda p: p.picking_type_code == "incoming" and not p.zc_qc_state):
            if picking.picking_type_id.warehouse_id in site_stores:
                picking.zc_qc_state = "pending"
        return res

    def action_zc_qc_accept(self):
        for picking in self:
            picking.write({"zc_qc_state": "accepted", "zc_qc_user_id": self.env.uid,
                           "zc_qc_date": fields.Date.context_today(self)})

    def action_zc_qc_reject(self):
        self.ensure_one()
        if not self.zc_qc_note:
            raise UserError(_("Write why the material is rejected (specification, damage, quantity...)."))
        self.write({"zc_qc_state": "rejected", "zc_qc_user_id": self.env.uid,
                    "zc_qc_date": fields.Date.context_today(self)})
        self.message_post(body=_("Rejected at technical inspection: %s", self.zc_qc_note))
        # open the standard return so the rejected material goes back to the vendor
        return {"type": "ir.actions.act_window", "res_model": "stock.return.picking", "view_mode": "form",
                "target": "new", "context": {"active_id": self.id, "active_ids": self.ids,
                                             "active_model": "stock.picking"}}
