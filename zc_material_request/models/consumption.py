from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_round


class ResCompany(models.Model):
    _inherit = "res.company"

    zc_issue_on_measurement = fields.Boolean(
        string="Issue materials from the site measurement",
        help="Each installed quantity measured on site prepares the issue of its materials from the site store "
             "(quantity x material per unit of the item's price analysis). The store keeper validates it.")
    zc_consumption_location_id = fields.Many2one("stock.location", string="Project consumption location",
                                                 domain="[('usage', '=', 'production')]")

    def _zc_consumption_location(self):
        self.ensure_one()
        if self.zc_consumption_location_id:
            return self.zc_consumption_location_id
        parent = self.env.ref("stock.stock_location_locations_virtual", raise_if_not_found=False)
        loc = self.env["stock.location"].sudo().search([("usage", "=", "production"),
                                                        ("name", "=", "Project Consumption"),
                                                        ("company_id", "in", (self.id, False))], limit=1)
        if not loc:
            loc = self.env["stock.location"].sudo().create({
                "name": "Project Consumption", "usage": "production", "company_id": self.id,
                "location_id": parent.id if parent else False})
        self.sudo().zc_consumption_location_id = loc
        return loc


class StockMove(models.Model):
    _inherit = "stock.move"

    zc_measurement_id = fields.Many2one("hom.site.measurement", string="Issued for measurement", index="btree_not_null",
                                        copy=False)


class HomSiteMeasurement(models.Model):
    _inherit = "hom.site.measurement"

    zc_issue_move_ids = fields.One2many("stock.move", "zc_measurement_id", string="Material issues")
    zc_issue_state = fields.Selection([("none", "No stock material"), ("todo", "Not issued"),
                                       ("pending", "Issue waiting for the store"), ("done", "Issued")],
                                      string="Material issue", compute="_compute_zc_issue_state")

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        auto = recs.filtered(lambda m: m.company_id.zc_issue_on_measurement)
        if auto:
            auto._zc_issue_materials(raise_if_empty=False)
        return recs

    def _zc_material_needs(self):
        """[(product, qty)] of stock materials consumed by this installed work, from the item's price analysis."""
        self.ensure_one()
        if "zc_stage" in self._fields and self.zc_stage == "supplied":
            return []
        out = []
        for cost in self.sale_line_id.sudo().zc_cost_line_ids:
            if cost.cost_type not in ("material", "accessory") or not cost.product_id.is_storable or not cost.qty_per_unit:
                continue
            qty = float_round(self.quantity * cost.qty_per_unit, precision_digits=4)
            if qty > 0:
                out.append((cost.product_id, qty))
        return out

    def _compute_zc_issue_state(self):
        for m in self:
            moves = m.zc_issue_move_ids.filtered(lambda mv: mv.state != "cancel")
            if not m._zc_material_needs():
                m.zc_issue_state = "none"
            elif not moves:
                m.zc_issue_state = "todo"
            elif all(mv.state == "done" for mv in moves):
                m.zc_issue_state = "done"
            else:
                m.zc_issue_state = "pending"

    def _zc_issue_materials(self, raise_if_empty=True, validate=False):
        """One issue voucher (إذن صرف) per site store: site store -> project consumption, a move per material and
        measurement, so every installed quantity has its material leaving the store exactly once."""
        todo = self.filtered(lambda m: not m.zc_issue_move_ids.filtered(lambda mv: mv.state != "cancel"))
        by_wh = defaultdict(list)
        for m in todo:
            project = m.project_id if "project_id" in m._fields else m.sale_line_id.order_id.project_ids[:1]
            wh = project.zc_warehouse_id
            needs = m._zc_material_needs()
            if not wh or not needs:
                continue
            by_wh[wh].extend((m, product, qty) for product, qty in needs)
        if not by_wh:
            if raise_if_empty:
                raise UserError(_("Nothing to issue: the work has no stock material in its price analysis, is already "
                                  "issued, or the project has no site store."))
            return self.env["stock.picking"]
        pickings = self.env["stock.picking"]
        for wh, rows in by_wh.items():
            company = wh.company_id
            dest = company._zc_consumption_location()
            picking = self.env["stock.picking"].sudo().create({
                "picking_type_id": wh.int_type_id.id, "location_id": wh.lot_stock_id.id,
                "location_dest_id": dest.id, "origin": _("Issue for work measured on site"),
                "move_ids": [Command.create({
                    "product_id": product.id, "product_uom_qty": qty, "location_id": wh.lot_stock_id.id,
                    "location_dest_id": dest.id, "zc_measurement_id": m.id,
                    "description_picking": "%s - %s" % (m.sale_line_id.product_id.display_name, m.location or "")})
                    for m, product, qty in rows]})
            picking.action_confirm()
            picking.action_assign()
            if validate:
                for move in picking.move_ids:
                    move.quantity = move.product_uom_qty
                picking.move_ids.picked = True
                picking.with_context(skip_backorder=True, picking_ids_not_to_backorder=picking.ids).button_validate()
            pickings |= picking
        return pickings

    def action_zc_issue_materials(self):
        pickings = self._zc_issue_materials()
        return {"type": "ir.actions.act_window", "name": _("Material issue"), "res_model": "stock.picking",
                "view_mode": "list,form", "domain": [("id", "in", pickings.ids)]}

    @api.model
    def _zc_unissued_qty(self, order, product):
        """Material already installed (measured) on this contract but not yet issued from the store."""
        meas = self.sudo().search([("sale_line_id.order_id", "=", order.id)])
        total = 0.0
        for m in meas:
            if m.zc_issue_move_ids.filtered(lambda mv: mv.state != "cancel"):
                continue
            for p, qty in m._zc_material_needs():
                if p == product:
                    total += qty
        return total
