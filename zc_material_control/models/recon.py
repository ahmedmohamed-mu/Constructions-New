from collections import defaultdict

from odoo import _, api, fields, models


class ZcMaterialRecon(models.Model):
    """Material Truth: for every site store and material, what the shop drawing needs, what was ordered and
    received, what the site installed and certified, and what is left on site — with the supplier return deadline.

    Rows are rebuilt from the live documents each time the report is opened (nothing is typed here).
    """
    _name = "zc.material.recon"
    _description = "Material Reconciliation"
    _order = "surplus_value desc, project_id, product_id"

    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    project_id = fields.Many2one("project.project", readonly=True)
    warehouse_id = fields.Many2one("stock.warehouse", string="Site Store", readonly=True)
    product_id = fields.Many2one("product.product", string="Material", readonly=True)
    categ_reusable = fields.Boolean(string="Reusable", readonly=True)
    uom_id = fields.Many2one("uom.uom", string="Unit", readonly=True)
    boq_items = fields.Char(string="BOQ Items", readonly=True)
    required_qty = fields.Float(string="Required (shop drawing)", readonly=True, digits="Product Unit")
    ordered_qty = fields.Float(string="Ordered", readonly=True, digits="Product Unit")
    received_qty = fields.Float(string="Received", readonly=True, digits="Product Unit")
    installed_qty = fields.Float(string="Installed", readonly=True, digits="Product Unit")
    certified_qty = fields.Float(string="Certified", readonly=True, digits="Product Unit")
    on_site_qty = fields.Float(string="In Site Store", readonly=True, digits="Product Unit")
    surplus_qty = fields.Float(string="Surplus on Site", readonly=True, digits="Product Unit")
    surplus_value = fields.Monetary(string="Surplus Value", readonly=True)
    over_order_qty = fields.Float(string="Ordered above Need", readonly=True, digits="Product Unit")
    return_deadline = fields.Date(readonly=True)
    days_left = fields.Integer(string="Days to Return", readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Last Receipt", readonly=True)
    recommendation = fields.Selection([("ok", "OK"), ("return", "Return to supplier"),
                                       ("transfer", "Transfer to another project"), ("hold", "Hold / sell"),
                                       ("shortage", "Installed more than received")],
                                      readonly=True)

    # ------------------------------------------------------------------
    @api.model
    def _zc_compute_rows(self):
        env = self.sudo().env
        today = fields.Date.context_today(self)
        rows = []
        projects = env["project.project"].search([("zc_warehouse_id", "!=", False)])
        for project in projects:
            wh = project.zc_warehouse_id
            order = project.reinvoiced_sale_order_id or project.sale_order_id
            per = defaultdict(lambda: defaultdict(float))
            items = defaultdict(set)
            # need and installed, from the item budget x site measurements
            for cost in (order.zc_cost_line_ids if order else env["zc.boq.cost.line"]).filtered("product_id"):
                p = cost.product_id
                boq = cost.sale_line_id
                installed, _supplied = boq._zc_measured()
                per[p]["required"] += cost.budget_qty
                per[p]["installed"] += installed * cost.qty_per_unit
                per[p]["certified"] += min(boq.qty_delivered, boq.product_uom_qty) * cost.qty_per_unit \
                    if not order.zc_stage_valuation else self._zc_certified_physical(boq) * cost.qty_per_unit
                items[p].add(boq.product_id.default_code or boq.product_id.name)
            # ordered / received: purchase lines for this project (linked item or project analytic)
            pol = env["purchase.order.line"].search([("order_id.state", "in", ("purchase", "done")),
                                                     ("product_id.is_storable", "=", True)])
            account = project.account_id
            for line in pol:
                linked = line.zc_boq_line_id and order and line.zc_boq_line_id.order_id == order
                analytic = account and any(str(account.id) in str(k).split(",")
                                           for k in (line.analytic_distribution or {}))
                if linked or analytic:
                    per[line.product_id]["ordered"] += line.uom_id._compute_quantity(
                        line.product_qty, line.product_id.uom_id)
            # what physically arrived in the site store and what left it
            moves = env["stock.move"].search([("state", "=", "done"), ("product_id.is_storable", "=", True),
                                              "|", ("location_dest_id", "child_of", wh.view_location_id.id),
                                              ("location_id", "child_of", wh.view_location_id.id)])
            last_receipt = {}
            for mv in moves:
                into = mv.location_dest_id.warehouse_id == wh
                out = mv.location_id.warehouse_id == wh
                if into and not out:
                    if mv.location_id.usage in ("supplier", "internal", "transit"):
                        per[mv.product_id]["received"] += mv.quantity
                    if mv.picking_id.picking_type_code == "incoming":
                        prev = last_receipt.get(mv.product_id)
                        if not prev or mv.picking_id.date_done > prev.date_done:
                            last_receipt[mv.product_id] = mv.picking_id
                elif out and not into and mv.location_dest_id.usage not in ("internal", "transit", "supplier"):
                    per[mv.product_id]["consumed"] += mv.quantity
            quants = env["stock.quant"].search([("location_id", "child_of", wh.view_location_id.id),
                                                ("location_id.usage", "=", "internal")])
            for q in quants:
                per[q.product_id]["on_site"] += q.quantity
            for product, v in per.items():
                if not product.is_storable:
                    continue
                # material on site not installed yet, minus what the remaining shop-drawing work still needs
                to_consume = max(v["installed"] - v["consumed"], 0.0)
                free_on_site = max(v["on_site"] - to_consume, 0.0)
                still_needed = max(v["required"] - v["installed"], 0.0)
                surplus = max(free_on_site - still_needed, 0.0)
                picking = last_receipt.get(product)
                deadline = picking.zc_return_deadline if picking else False
                days_left = (deadline - today).days if deadline else 0
                reusable = product.categ_id.zc_reusable
                if v["on_site"] + v["consumed"] + 1e-6 < v["installed"] and v["installed"]:
                    rec = "shortage"
                elif surplus <= 1e-6:
                    rec = "ok"
                elif deadline and days_left >= 0:
                    rec = "return"
                elif reusable:
                    rec = "transfer"
                else:
                    rec = "hold"
                rows.append({
                    "company_id": project.company_id.id, "project_id": project.id, "warehouse_id": wh.id,
                    "product_id": product.id, "product_name": product.display_name, "uom": product.uom_id.name,
                    "uom_id": product.uom_id.id, "categ_reusable": reusable,
                    "boq_items": ", ".join(sorted(items.get(product, []))),
                    "required_qty": v["required"], "ordered_qty": v["ordered"], "received_qty": v["received"],
                    "installed_qty": v["installed"], "certified_qty": v["certified"], "on_site_qty": v["on_site"],
                    "surplus_qty": surplus, "surplus_value": surplus * product.standard_price,
                    "over_order_qty": max(v["ordered"] - v["required"], 0.0) if v["required"] else 0.0,
                    "return_deadline": deadline, "days_left": days_left,
                    "picking_id": picking.id if picking else False, "recommendation": rec,
                })
        return rows

    @api.model
    def _zc_certified_physical(self, boq):
        """Physical installed quantity covered by approved / invoiced certificates."""
        lines = self.env["hom.progress.certificate.line"].sudo().search([
            ("sale_line_id", "=", boq.id), ("certificate_id.state", "in", ("approved", "invoiced"))],
            order="id desc", limit=1)
        if not lines:
            return 0.0
        installed, _supplied = boq._zc_measured(lines.certificate_id.period_end or lines.certificate_id.date)
        return installed

    @api.model
    def action_refresh(self):
        rows = self._zc_compute_rows()
        self.sudo().search([]).unlink()
        keys = set(self._fields)
        self.sudo().create([{k: v for k, v in r.items() if k in keys} for r in rows])
        return {
            "type": "ir.actions.act_window", "name": _("Material Truth"), "res_model": self._name,
            "views": [[self.env.ref("zc_material_control.zc_material_recon_list").id, "list"],
                      [False, "pivot"], [False, "form"]],
            "target": "current", "context": {"search_default_with_surplus": 1, "search_default_group_project": 1},
        }
