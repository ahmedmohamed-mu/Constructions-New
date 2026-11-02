from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init
from odoo.addons.zc_project_controls.hooks import _zc_pc_post_init
from odoo.addons.zc_certificate_controls.hooks import _zc_cc_post_init


@tagged("post_install", "-at_install")
class TestItemCard(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("analytic.group_analytic_accounting") \
            | env.ref("account.group_account_manager") | env.ref("stock.group_stock_manager")
        company = env.company
        ref = lambda x: env.ref("hom_contracting_base.%s" % x)
        company.write({"hom_retention_owner_product_id": ref("product_retention_owner").id,
                       "hom_retention_sub_product_id": ref("product_retention_sub").id,
                       "hom_sub_advance_product_id": ref("product_sub_advance").id,
                       "hom_sub_advance_recovery_product_id": ref("product_sub_advance_recovery").id,
                       "zc_price_tolerance_pct": 0.0})
        _zc_post_init(env)
        _zc_pc_post_init(env)
        _zc_cc_post_init(env)
        cls.owner = env["res.partner"].create({"name": "Owner (demo)"})
        cls.supplier = env["res.partner"].create({"name": "Tile supplier", "supplier_rank": 1})
        cls.sub = env["res.partner"].create({"name": "Tiling crew", "supplier_rank": 1})
        cls.item = env["product.product"].create({
            "name": "FN-01 Ceramic floor", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only", "taxes_id": [Command.clear()]})
        cls.tile = env["product.product"].create({"name": "Ceramic tile 60x60", "type": "consu",
                                                  "supplier_taxes_id": [Command.clear()]})
        cls.labour = env["product.product"].create({"name": "Tile laying", "type": "service",
                                                    "purchase_method": "purchase",
                                                    "supplier_taxes_id": [Command.clear()]})

    def _pay(self, move):
        self.env["account.payment.register"].with_context(active_model="account.move", active_ids=move.ids) \
            .create({})._create_payments()

    def test_item_card_full_cycle(self):
        env = self.env
        today = fields.Date.context_today(env.user)
        so = env["sale.order"].create({
            "partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 0,
            "order_line": [Command.create({"product_id": self.item.id, "product_uom_qty": 100, "price_unit": 450,
                                           "tax_ids": [Command.clear()]})]})
        so.action_confirm()
        project = so.project_ids[:1]
        if not project.account_id:
            plan = env["account.analytic.plan"].search([], limit=1)
            project.account_id = env["account.analytic.account"].create({"name": "P", "plan_id": plan.id})
        sol = so.order_line
        # price analysis: tiles with 5% waste at 260 + laying at 70 (no product: the subcontractor's rate)
        env["zc.boq.cost.line"].create([
            {"sale_line_id": sol.id, "cost_type": "material", "product_id": self.tile.id, "qty_per_unit": 1.05,
             "unit_cost": 260},
            {"sale_line_id": sol.id, "cost_type": "labour", "name": "Laying", "unit_cost": 70}])
        sol.invalidate_recordset()
        self.assertAlmostEqual(sol.zc_ic_budget_cost, 34300)
        self.assertAlmostEqual(sol.zc_ic_budget_margin, 10700)

        # material bought within budget
        po = env["purchase.order"].create({"partner_id": self.supplier.id, "order_line": [Command.create({
            "product_id": self.tile.id, "product_qty": 100, "price_unit": 260, "zc_boq_line_id": sol.id})]})
        self.assertEqual(po.order_line.zc_check, "ok")
        po.button_confirm()

        # subcontract linked to the item only through back-to-back: synced to the budget check, 80 > 70
        sc = env["purchase.order"].create({"partner_id": self.sub.id, "hom_is_subcontract": True, "order_line": [
            Command.create({"product_id": self.labour.id, "product_qty": 100, "price_unit": 80,
                            "hom_boq_line_id": sol.id})]})
        self.assertEqual(sc.order_line.zc_boq_line_id, sol)
        self.assertEqual(sc.order_line.zc_check, "over_price")
        self.assertTrue(sc.zc_needs_exception)
        with self.assertRaises(UserError):
            sc.button_confirm()
        sc.zc_exception_reason = "Only crew available for the handover date"
        sc.action_zc_request_exception()
        sc.action_zc_approve_exception()
        sc.button_confirm()
        self.assertEqual(sc.state, "purchase")
        labour_cost = sol.zc_cost_line_ids.filtered(lambda c: c.cost_type == "labour")
        labour_cost.invalidate_recordset()
        self.assertAlmostEqual(labour_cost.committed_amount, 8000)

        # site: 60 executed, owner certificate, invoice, collected
        env["hom.site.measurement"].create({"sale_line_id": sol.id, "quantity": 60, "location": "GF"})
        cert = env["hom.progress.certificate"].create({"sale_order_id": so.id})
        cert.action_load_boq()
        cert.action_submit()
        cert.action_approve()
        if not cert.invoice_id:
            cert.action_create_invoice()
        inv = cert.invoice_id
        inv.invoice_date = inv.invoice_date or today
        inv.action_post()

        # subcontractor certificate back-to-back, billed and paid
        scert = env["hom.subcontract.certificate"].create({"purchase_order_id": sc.id})
        scert.action_load_items()
        self.assertAlmostEqual(scert.line_ids.cumulative_qty, 60)
        scert.action_submit()
        if scert.zc_review_stage == "qs":
            scert.action_zc_qs_check()
        scert.action_approve()
        scert.action_create_bill()
        bill = scert.bill_id
        bill.invoice_date = bill.invoice_date or today
        bill.action_post()

        sol.invalidate_recordset()
        self.assertAlmostEqual(sol.zc_ic_committed, 34000)
        self.assertEqual(sol.zc_ic_over_budget_lines, 1)
        # forecast: tiles 26,000 + 5 m2 still to buy at 260 + laying 8,000
        self.assertAlmostEqual(sol.zc_ic_forecast_cost, 35300)
        self.assertAlmostEqual(sol.zc_ic_forecast_margin, 9700)
        self.assertAlmostEqual(sol.zc_ic_margin_drift, -1000)
        self.assertEqual(sol.zc_ic_status, "warn")
        self.assertAlmostEqual(sol.zc_ic_measured_qty, 60)
        self.assertAlmostEqual(sol.qty_delivered, 60)
        self.assertAlmostEqual(sol.zc_ic_certified_amount, 27000)
        self.assertAlmostEqual(sol.zc_ic_invoiced_amount, 27000)
        self.assertAlmostEqual(sol.zc_ic_collected_amount, 0)
        self.assertAlmostEqual(sol.zc_ic_sub_certified_qty, 60)
        self.assertAlmostEqual(sol.zc_ic_sub_certified_amount, 4800)
        self.assertAlmostEqual(sol.zc_ic_billed_cost, 4800)
        self.assertEqual(sol.zc_ic_sub_partner_ids, self.sub)
        self.assertEqual((sol.zc_ic_cert_count, sol.zc_ic_sub_cert_count, sol.zc_ic_invoice_count,
                          sol.zc_ic_bill_count, sol.zc_ic_po_count), (1, 1, 1, 1, 2))

        self._pay(inv)
        self._pay(bill)
        sol.invalidate_recordset()
        self.assertAlmostEqual(sol.zc_ic_collected_amount, 27000)
        self.assertAlmostEqual(sol.zc_ic_paid_cost, 4800)

        # every button opens its documents; the card and the list open from the contract
        for name in ("action_zc_ic_purchases", "action_zc_ic_measurements", "action_zc_ic_certificates",
                     "action_zc_ic_sub_certificates", "action_zc_ic_invoices", "action_zc_ic_bills",
                     "action_zc_ic_disputes", "action_zc_ic_variations", "action_zc_ic_wirs"):
            action = getattr(sol, name)()
            self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(sol.action_zc_item_card()["res_id"], sol.id)
        self.assertEqual(so.zc_ic_item_count, 1)
        self.assertIn(("order_id", "=", so.id), so.action_zc_item_cards()["domain"])
        env["sale.order.line"].get_views([(env.ref("zc_boq_item_card.view_zc_item_card_form").id, "form"),
                                          (env.ref("zc_boq_item_card.view_zc_item_card_list").id, "list")])
