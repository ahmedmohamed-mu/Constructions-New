from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


class ZcMaterialRequest(models.Model):
    _name = "zc.material.request"
    _description = "Site Material Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True, check_company=True,
                                    domain="[('state', '=', 'sale'), ('hom_project_type', '=', 'contracting')]",
                                    tracking=True)
    project_id = fields.Many2one("project.project", compute="_compute_project", store=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Site store", compute="_compute_project", store=True,
                                   readonly=False)
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    user_id = fields.Many2one("res.users", string="Requested by", default=lambda self: self.env.user, tracking=True)
    date = fields.Date(default=fields.Date.context_today, required=True)
    date_needed = fields.Date(string="Needed on site by", required=True, tracking=True)
    location = fields.Char(string="Zone / floor")
    state = fields.Selection([("draft", "Draft"), ("submitted", "Waiting PM approval"), ("approved", "Approved"),
                              ("done", "Served / ordered"), ("cancel", "Cancelled")], default="draft", tracking=True,
                             copy=False)
    line_ids = fields.One2many("zc.material.request.line", "request_id", copy=True)
    picking_ids = fields.Many2many("stock.picking", string="Transfers", readonly=True, copy=False)
    purchase_ids = fields.Many2many("purchase.order", string="Requests for quotation", readonly=True, copy=False)
    picking_count = fields.Integer(compute="_compute_counts")
    purchase_count = fields.Integer(compute="_compute_counts")
    late = fields.Boolean(compute="_compute_late")

    @api.depends("sale_order_id")
    def _compute_project(self):
        for rec in self:
            project = rec.sale_order_id.project_ids[:1]
            rec.project_id = project
            rec.warehouse_id = project.zc_warehouse_id or rec.warehouse_id

    def _compute_counts(self):
        for rec in self:
            rec.picking_count = len(rec.picking_ids)
            rec.purchase_count = len(rec.purchase_ids)

    def _compute_late(self):
        today = fields.Date.context_today(self)
        for rec in self:
            rec.late = rec.state in ("submitted", "approved") and rec.date_needed and rec.date_needed < today

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("zc.material.request") or _("New")
        return super().create(vals_list)

    def action_submit(self):
        for rec in self:
            if not rec.line_ids:
                raise UserError(_("Add the materials you need."))
            rec.state = "submitted"
            pm = rec.project_id.user_id
            if pm:
                rec.activity_schedule("mail.mail_activity_data_todo", user_id=pm.id, date_deadline=rec.date_needed,
                                      summary=_("Approve material request %s", rec.name))

    def action_approve(self):
        for rec in self.filtered(lambda r: r.state == "submitted"):
            over = rec.line_ids.filtered(lambda l: l.budget_check == "over")
            if over and not rec.env.context.get("zc_mr_force"):
                reasons = ", ".join(over.mapped("product_id.display_name"))
                rec.message_post(body=_("Approved above the item budget for: %s", reasons))
            rec.state = "approved"
            rec.activity_feedback(["mail.mail_activity_data_todo"])

    def action_cancel(self):
        self.write({"state": "cancel"})

    def action_draft(self):
        self.filtered(lambda r: r.state in ("submitted", "cancel")).write({"state": "draft"})

    def action_process(self):
        """Serve from other stores first (internal transfer to the site store), buy the rest (RFQ per vendor)."""
        for rec in self:
            if rec.state != "approved":
                raise UserError(_("Only an approved request can be processed."))
            if not rec.warehouse_id:
                raise UserError(_("Set the site store of the project (Project > Site Store) first."))
            rec._create_site_issue()
            rec._create_transfers()
            rec._create_rfqs()
            rec.state = "done"
        return True

    def _create_site_issue(self):
        """What the site store can serve leaves it now: issue voucher site store -> project consumption."""
        self.ensure_one()
        site = self.warehouse_id
        rows = [(line, min(line.quantity, line.available_site_qty)) for line in self.line_ids
                if line.product_id.is_storable and line.available_site_qty > 0]
        rows = [(line, qty) for line, qty in rows if qty > 0]
        if not rows:
            return
        dest = self.company_id._zc_consumption_location()
        picking = self.env["stock.picking"].create({
            "picking_type_id": site.int_type_id.id, "location_id": site.lot_stock_id.id,
            "location_dest_id": dest.id, "origin": self.name,
            "move_ids": [Command.create({
                "product_id": line.product_id.id, "product_uom_qty": qty, "location_id": site.lot_stock_id.id,
                "location_dest_id": dest.id, "description_picking": line.note or line.product_id.display_name})
                for line, qty in rows]})
        picking.action_confirm()
        picking.action_assign()
        self.picking_ids |= picking
        for line, qty in rows:
            line.issued_qty += qty

    def _create_transfers(self):
        self.ensure_one()
        site = self.warehouse_id
        Quant = self.env["stock.quant"].sudo()
        moves_by_wh = {}
        for line in self.line_ids.filtered(lambda l: l.product_id.is_storable):
            need = min(max(line.quantity - line.issued_qty, 0.0), line.available_other_qty)
            if need <= 0:
                continue
            quants = Quant.search([("product_id", "=", line.product_id.id), ("location_id.usage", "=", "internal"),
                                   ("company_id", "=", self.company_id.id), ("quantity", ">", 0)])
            for q in quants:
                wh = q.location_id.warehouse_id
                if not wh or wh == site or need <= 0:
                    continue
                free = q.quantity - q.reserved_quantity
                take = min(free, need)
                if take <= 0:
                    continue
                moves_by_wh.setdefault(wh, []).append((line, take, q.location_id))
                need -= take
        for wh, items in moves_by_wh.items():
            picking = self.env["stock.picking"].create({
                "picking_type_id": wh.int_type_id.id, "location_id": wh.lot_stock_id.id,
                "location_dest_id": site.lot_stock_id.id, "origin": self.name,
                "move_ids": [Command.create({
                    "product_id": line.product_id.id, "product_uom_qty": qty,
                    "location_id": loc.id, "location_dest_id": site.lot_stock_id.id,
                    "description_picking": line.note or line.product_id.display_name}) for line, qty, loc in items]})
            picking.action_confirm()
            self.picking_ids |= picking
            for line, qty, _loc in items:
                line.transferred_qty += qty

    def _create_rfqs(self):
        self.ensure_one()
        by_vendor = {}
        account = self.project_id.account_id
        for line in self.line_ids:
            qty = line.quantity - line.transferred_qty - line.issued_qty
            if float_compare(qty, 0.0, precision_digits=4) <= 0:
                continue
            vendor = line.product_id.seller_ids[:1].partner_id
            by_vendor.setdefault(vendor, []).append((line, qty))
        for vendor, items in by_vendor.items():
            if not vendor:
                buyer = self.env.ref("purchase.group_purchase_user", raise_if_not_found=False)
                users = buyer.user_ids[:1] if buyer else self.env.user
                self.activity_schedule("mail.mail_activity_data_todo", user_id=(users or self.env.user).id,
                                       date_deadline=self.date_needed,
                                       summary=_("Source a vendor for: %s",
                                                 ", ".join(l.product_id.display_name for l, _q in items)))
                continue
            po = self.env["purchase.order"].create({
                "partner_id": vendor.id, "origin": self.name,
                "picking_type_id": self.warehouse_id.in_type_id.id,
                "date_planned": fields.Datetime.to_datetime(self.date_needed),
                "order_line": [Command.create({
                    "product_id": line.product_id.id, "product_qty": qty, "zc_boq_line_id": line.sale_line_id.id,
                    "analytic_distribution": {str(account.id): 100} if account else False,
                    "date_planned": fields.Datetime.to_datetime(self.date_needed)}) for line, qty in items]})
            self.purchase_ids |= po
            for line, qty in items:
                line.ordered_qty += qty

    def action_view_pickings(self):
        return {"type": "ir.actions.act_window", "name": _("Transfers"), "res_model": "stock.picking",
                "view_mode": "list,form", "domain": [("id", "in", self.picking_ids.ids)]}

    def action_view_purchases(self):
        return {"type": "ir.actions.act_window", "name": _("Requests for quotation"), "res_model": "purchase.order",
                "view_mode": "list,form", "domain": [("id", "in", self.purchase_ids.ids)]}


class ZcMaterialRequestLine(models.Model):
    _name = "zc.material.request.line"
    _description = "Site Material Request Line"

    request_id = fields.Many2one("zc.material.request", required=True, ondelete="cascade", index=True)
    sale_order_id = fields.Many2one(related="request_id.sale_order_id")
    sale_line_id = fields.Many2one("sale.order.line", string="For BOQ item",
                                   domain="[('order_id', '=', sale_order_id), ('display_type', '=', False)]")
    product_id = fields.Many2one("product.product", string="Material", required=True)
    uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    quantity = fields.Float(required=True, digits="Product Unit")
    note = fields.Char()
    available_site_qty = fields.Float(string="In site store", compute="_compute_stock", digits="Product Unit")
    available_other_qty = fields.Float(string="In other stores", compute="_compute_stock", digits="Product Unit")
    to_transfer_qty = fields.Float(string="To transfer", compute="_compute_stock", digits="Product Unit")
    allowed_qty = fields.Float(string="Still allowed by budget", compute="_compute_budget", digits="Product Unit")
    budget_check = fields.Selection([("ok", "Within budget"), ("over", "Above budget"), ("none", "No item budget")],
                                    compute="_compute_budget")
    transferred_qty = fields.Float(readonly=True, copy=False, digits="Product Unit")
    issued_qty = fields.Float(string="Issued from site store", readonly=True, copy=False, digits="Product Unit")
    ordered_qty = fields.Float(string="Ordered", readonly=True, copy=False, digits="Product Unit")

    @api.depends("product_id", "quantity", "request_id.warehouse_id")
    def _compute_stock(self):
        Quant = self.env["stock.quant"].sudo()
        for line in self:
            site = line.request_id.warehouse_id
            on_site = other = 0.0
            if line.product_id.is_storable:
                for q in Quant.search([("product_id", "=", line.product_id.id), ("location_id.usage", "=", "internal"),
                                       ("quantity", ">", 0)]):
                    free = q.quantity - q.reserved_quantity
                    if site and q.location_id.warehouse_id == site:
                        on_site += free
                    elif q.location_id.warehouse_id:
                        other += free
            if on_site and line.request_id.sale_order_id:
                # material already laid on this contract but not issued yet is not really available
                on_site = max(on_site - self.env["hom.site.measurement"]._zc_unissued_qty(
                    line.request_id.sale_order_id, line.product_id), 0.0)
            line.available_site_qty = on_site
            line.available_other_qty = other
            line.to_transfer_qty = min(max(line.quantity - on_site, 0.0), other)

    @api.depends("product_id", "quantity", "sale_line_id")
    def _compute_budget(self):
        for line in self:
            cost = line.sale_line_id.zc_cost_line_ids.filtered(lambda c: c.product_id == line.product_id)[:1] \
                if line.sale_line_id else False
            if not cost:
                line.allowed_qty = 0.0
                line.budget_check = "none"
                continue
            line.allowed_qty = max(cost.allowed_qty - cost.ordered_qty, 0.0)
            need = max(line.quantity - line.available_site_qty, 0.0)
            line.budget_check = "over" if float_compare(need, line.allowed_qty, precision_digits=4) > 0 \
                and not float_is_zero(need, precision_digits=4) else "ok"
