from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init


@tagged("post_install", "-at_install")
class TestMaterialRequest(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("stock.group_stock_manager") \
            | env.ref("analytic.group_analytic_accounting")
        _zc_post_init(env)
        cls.owner = env["res.partner"].create({"name": "Owner"})
        cls.vendor = env["res.partner"].create({"name": "Tile vendor", "supplier_rank": 1})
        cls.item = env["product.product"].create({
            "name": "FN-01 Ceramic floor", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only"})
        cls.tile = env["product.product"].create({
            "name": "Ceramic tile", "type": "consu", "is_storable": True, "standard_price": 260,
            "seller_ids": [Command.create({"partner_id": cls.vendor.id, "price": 260})]})
        cls.site = env["stock.warehouse"].create({"name": "Site MR", "code": "SMR"})
        cls.main = env["stock.warehouse"].search([("company_id", "=", env.company.id), ("id", "!=", cls.site.id)],
                                                 limit=1)

    def test_request_transfer_then_buy_then_accept(self):
        env = self.env
        so = env["sale.order"].create({"partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 0,
                                       "order_line": [Command.create({"product_id": self.item.id,
                                                                      "product_uom_qty": 100, "price_unit": 450})]})
        so.action_confirm()
        project = so.project_ids[:1]
        if not project.account_id:
            plan = env["account.analytic.plan"].search([], limit=1)
            project.account_id = env["account.analytic.account"].create({"name": "MR", "plan_id": plan.id})
        project.zc_warehouse_id = self.site
        sol = so.order_line
        env["zc.boq.cost.line"].create({"sale_line_id": sol.id, "product_id": self.tile.id, "qty_per_unit": 1.05,
                                        "unit_cost": 260})
        # 10 m2 already on site, 30 m2 in the main store
        env["stock.quant"]._update_available_quantity(self.tile, self.site.lot_stock_id, 10)
        env["stock.quant"]._update_available_quantity(self.tile, self.main.lot_stock_id, 30)

        today = fields.Date.context_today(env.user)
        mr = env["zc.material.request"].create({
            "sale_order_id": so.id, "date_needed": today + timedelta(days=3),
            "line_ids": [Command.create({"sale_line_id": sol.id, "product_id": self.tile.id, "quantity": 60})]})
        self.assertEqual(mr.warehouse_id, self.site)
        line = mr.line_ids
        self.assertEqual((line.available_site_qty, line.available_other_qty, line.to_transfer_qty), (10, 30, 30))
        self.assertEqual(line.budget_check, "ok")
        with self.assertRaises(UserError):
            mr.action_process()
        mr.action_submit()
        mr.action_approve()
        mr.action_process()
        self.assertEqual(mr.state, "done")
        # 10 issued from the site store to the work, 30 transferred from the main store,
        # 60 - 10 - 30 = 20 bought from the vendor, checked against the item
        consumption = env.company._zc_consumption_location()
        issue = mr.picking_ids.filtered(lambda p: p.location_dest_id == consumption)
        transfer = mr.picking_ids.filtered(lambda p: p.location_dest_id == self.site.lot_stock_id)
        self.assertEqual((len(issue), len(transfer)), (1, 1))
        self.assertAlmostEqual(issue.move_ids.product_uom_qty, 10)
        self.assertAlmostEqual(mr.line_ids.issued_qty, 10)
        self.assertAlmostEqual(sum(transfer.move_ids.mapped("product_uom_qty")), 30)
        for move in issue.move_ids:
            move.quantity = move.product_uom_qty
        issue.move_ids.picked = True
        issue.button_validate()
        self.assertAlmostEqual(self.tile.with_context(warehouse_id=self.site.id).qty_available, 0,
                               msg="the store really goes down when the issue is validated")
        po = mr.purchase_ids
        self.assertEqual(po.partner_id, self.vendor)
        self.assertAlmostEqual(po.order_line.product_qty, 20)
        self.assertEqual(po.order_line.zc_boq_line_id, sol)
        self.assertEqual(po.order_line.zc_check, "ok")
        self.assertEqual(po.picking_type_id, self.site.in_type_id)

        # the receipt on site waits for the technical acceptance
        po.button_confirm()
        receipt = po.picking_ids
        for move in receipt.move_ids:
            move.quantity = move.product_uom_qty
        receipt.move_ids.picked = True
        receipt.button_validate()
        self.assertEqual(receipt.zc_qc_state, "pending")
        with self.assertRaises(UserError):
            receipt.action_zc_qc_reject()
        receipt.zc_qc_note = "Shade differs from the approved sample"
        action = receipt.action_zc_qc_reject()
        self.assertEqual(action["res_model"], "stock.return.picking")
        self.assertEqual(receipt.zc_qc_state, "rejected")

    def test_over_budget_is_flagged(self):
        env = self.env
        so = env["sale.order"].create({"partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 0,
                                       "order_line": [Command.create({"product_id": self.item.id,
                                                                      "product_uom_qty": 10, "price_unit": 450})]})
        so.action_confirm()
        env["zc.boq.cost.line"].create({"sale_line_id": so.order_line.id, "product_id": self.tile.id,
                                        "qty_per_unit": 1.0, "unit_cost": 260})
        mr = env["zc.material.request"].create({
            "sale_order_id": so.id, "date_needed": fields.Date.context_today(env.user),
            "line_ids": [Command.create({"sale_line_id": so.order_line.id, "product_id": self.tile.id,
                                         "quantity": 500})]})
        self.assertEqual(mr.line_ids.budget_check, "over")
        mr.action_submit()
        mr.action_approve()
        self.assertTrue(any(self.tile.name in (m.body or "") for m in mr.message_ids), "approval above budget is logged")

    def test_measured_work_issues_its_material(self):
        env = self.env
        env.company.zc_issue_on_measurement = True
        so = env["sale.order"].create({"partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 0,
                                       "order_line": [Command.create({"product_id": self.item.id,
                                                                      "product_uom_qty": 100, "price_unit": 450})]})
        so.action_confirm()
        project = so.project_ids[:1]
        project.zc_warehouse_id = self.site
        sol = so.order_line
        env["zc.boq.cost.line"].create({"sale_line_id": sol.id, "product_id": self.tile.id, "qty_per_unit": 1.05,
                                        "unit_cost": 260})
        env["stock.quant"]._update_available_quantity(self.tile, self.site.lot_stock_id, 100)
        stock = lambda: self.tile.with_context(warehouse_id=self.site.id).qty_available
        self.assertAlmostEqual(stock(), 100)

        # 40 m2 laid: the issue voucher for 42 m2 of tiles (5% waste) is prepared automatically
        m = env["hom.site.measurement"].create({"sale_line_id": sol.id, "quantity": 40, "location": "GF"})
        self.assertEqual(m.zc_issue_state, "pending")
        picking = m.zc_issue_move_ids.picking_id
        self.assertEqual(picking.location_dest_id, env.company._zc_consumption_location())
        self.assertAlmostEqual(m.zc_issue_move_ids.product_uom_qty, 42)
        self.assertAlmostEqual(stock(), 100, msg="nothing leaves before the store keeper validates")
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
        picking.move_ids.picked = True
        picking.button_validate()
        m.invalidate_recordset()
        self.assertEqual(m.zc_issue_state, "done")
        self.assertAlmostEqual(stock(), 58)

        # work measured without the automatic issue: not available for a new request, flagged, then issued
        env.company.zc_issue_on_measurement = False
        m2 = env["hom.site.measurement"].create({"sale_line_id": sol.id, "quantity": 20, "location": "1F"})
        self.assertEqual(m2.zc_issue_state, "todo")
        mr = env["zc.material.request"].create({
            "sale_order_id": so.id, "date_needed": fields.Date.context_today(env.user),
            "line_ids": [Command.create({"sale_line_id": sol.id, "product_id": self.tile.id, "quantity": 10})]})
        self.assertAlmostEqual(mr.line_ids.available_site_qty, 58 - 21,
                               msg="21 m2 already laid but not issued are not available")
        alerts = env["hom.control.tower"]._alerts(env.company, fields.Date.context_today(env.user))
        self.assertTrue(any(isinstance(a.get("action"), dict) and m2.id in a["action"]["domain"][0][2]
                            for a in alerts if a.get("action")))
        m2.action_zc_issue_materials()
        m2.invalidate_recordset()
        self.assertEqual(m2.zc_issue_state, "pending")
        with self.assertRaises(UserError):
            m2.action_zc_issue_materials()  # never issued twice
