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
        # 30 transferred from the main store, 60 - 10 - 30 = 20 bought from the vendor, checked against the item
        self.assertEqual(len(mr.picking_ids), 1)
        self.assertEqual(mr.picking_ids.location_dest_id, self.site.lot_stock_id)
        self.assertAlmostEqual(sum(mr.picking_ids.move_ids.mapped("product_uom_qty")), 30)
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
