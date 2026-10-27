from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..hooks import _zc_post_init


@tagged("post_install", "-at_install")
class TestMaterialControl(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("stock.group_stock_manager") \
            | env.ref("analytic.group_analytic_accounting")
        cls.today = fields.Date.context_today(env.user)
        cls.tax14 = env["account.tax"].create({"name": "VAT 14 S", "amount": 14, "type_tax_use": "sale"})
        cls.retention_account = cls.company_data["default_account_receivable"].copy({
            "code": "102020", "name": "Retention receivable", "account_type": "asset_current", "reconcile": False})
        cls.dp_account = cls.company_data["default_account_revenue"].copy({
            "code": "201030", "name": "Customer advances", "account_type": "liability_current"})
        env.company.downpayment_account_id = cls.dp_account
        retention = env.ref("hom_contracting_base.product_retention_owner")
        env.company.hom_retention_owner_product_id = retention
        retention.product_tmpl_id.property_account_income_id = False
        _zc_post_init(env)  # the hook books the retention on 102020
        cls.revenue = cls.company_data["default_account_revenue"]
        # stores
        cls.yard = env["stock.warehouse"].search([("company_id", "=", env.company.id)], limit=1)
        cls.site = env["stock.warehouse"].create({"name": "Site B4", "code": "TB4"})
        # material categories
        cls.cat_generic = env["product.category"].create({"name": "Generic", "zc_reusable": True, "zc_tolerance_pct": 2})
        cls.cat_model = env["product.category"].create({"name": "Model", "zc_reusable": False})
        cls.vendor = env["res.partner"].create({"name": "Lights vendor", "zc_return_days": 14})
        mat = dict(type="consu", is_storable=True)
        cls.led = env["product.product"].create({"name": "LED panel", "categ_id": cls.cat_model.id,
                                                 "standard_price": 2000, **mat})
        cls.paint = env["product.product"].create({"name": "Paint", "categ_id": cls.cat_generic.id,
                                                   "standard_price": 130, **mat})
        boq = dict(type="service", service_type="manual", invoice_policy="delivery", service_tracking="project_only",
                   taxes_id=[Command.set(cls.tax14.ids)], property_account_income_id=cls.revenue.id)
        cls.item_light = env["product.product"].create({"name": "EL-01 Light fitting", "default_code": "EL-01", **boq})
        cls.item_paint = env["product.product"].create({"name": "FN-03 Paint works", "default_code": "FN-03", **boq})
        cls.labour = env["product.product"].create({"name": "Install lights", "type": "service"})
        cls.pm = env["res.users"].create({"name": "PM", "login": "zc_pm",
                                          "group_ids": [Command.set([env.ref("project.group_project_user").id,
                                                                     env.ref("purchase.group_purchase_user").id])]})
        cls.so = env["sale.order"].create({
            "partner_id": cls.partner_a.id, "hom_advance_pct": 15, "hom_retention_pct": 5,
            "zc_stage_valuation": True, "zc_supplied_pct": 60, "zc_installed_pct": 75,
            "order_line": [
                Command.create({"product_id": cls.item_light.id, "product_uom_qty": 100, "price_unit": 3000}),
                Command.create({"product_id": cls.item_paint.id, "product_uom_qty": 1000, "price_unit": 240}),
            ]})
        cls.so.action_confirm()
        cls.sol_light = cls.so.order_line.filtered(lambda l: l.product_id == cls.item_light)
        cls.sol_paint = cls.so.order_line.filtered(lambda l: l.product_id == cls.item_paint)
        cls.project = cls.so.project_ids[:1]
        if not cls.project.account_id:
            plan = env["account.analytic.plan"].search([], limit=1) or env["account.analytic.plan"].create({"name": "P"})
            cls.project.account_id = env["account.analytic.account"].create({"name": "B4", "plan_id": plan.id})
        cls.project.write({"zc_warehouse_id": cls.site.id, "user_id": cls.pm.id})
        Cost = env["zc.boq.cost.line"]
        Cost.create([
            {"sale_line_id": cls.sol_light.id, "cost_type": "material", "product_id": cls.led.id, "unit_cost": 2000},
            {"sale_line_id": cls.sol_light.id, "cost_type": "labour", "product_id": cls.labour.id, "unit_cost": 100},
            {"sale_line_id": cls.sol_paint.id, "cost_type": "material", "product_id": cls.paint.id,
             "qty_per_unit": 0.17, "unit_cost": 130},
        ])
        cls.dist = {str(cls.project.account_id.id): 100}

    # ------------------------------------------------------------------ helpers
    def _po(self, product, qty, price, boq=None, wh=None, partner=None, reason=None):
        po = self.env["purchase.order"].create({
            "partner_id": (partner or self.vendor).id, "picking_type_id": (wh or self.site).in_type_id.id,
            "order_line": [Command.create({"product_id": product.id, "product_qty": qty, "price_unit": price,
                                           "analytic_distribution": self.dist,
                                           **({"zc_boq_line_id": boq.id} if boq else {})})]})
        if reason:
            po.zc_exception_reason = reason
        return po

    def _receive(self, po, days_ago=0):
        for picking in po.picking_ids.filtered(lambda p: p.state not in ("done", "cancel")):
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
            picking.move_ids.picked = True
            picking.with_context(skip_backorder=True).button_validate()
            if days_ago:
                picking.date_done = fields.Datetime.now() - timedelta(days=days_ago)
        return po.picking_ids

    def _measure(self, sol, qty, stage, days_ago=0):
        return self.env["hom.site.measurement"].create({"sale_line_id": sol.id, "quantity": qty, "zc_stage": stage,
                                                        "date": self.today - timedelta(days=days_ago)})

    def _approve_shop_drawing(self, sol, qty):
        sub = self.env["hom.submittal"].create({"title": "SD", "submittal_type": "shop_drawing",
                                                "project_id": self.project.id, "zc_sale_line_id": sol.id,
                                                "zc_sd_qty": qty, "responsible_id": self.pm.id})
        sub.action_submit()
        sub.action_code_a()
        return sub

    # ------------------------------------------------------------------ tests
    def test_shop_drawing_quantity_drives_budget(self):
        cost = self.sol_light.zc_cost_line_ids.filtered(lambda c: c.product_id == self.led)
        self.assertEqual(cost.budget_qty, 100, "without a shop drawing the BOQ quantity is the basis")
        sub = self._approve_shop_drawing(self.sol_light, 97)
        self.assertEqual(self.sol_light.zc_sd_qty, 97)
        self.assertEqual(self.sol_light.zc_sd_submittal_id, sub)
        self.assertEqual(cost.budget_qty, 97)
        self.assertEqual(self.sol_light.zc_unit_cost, 2100)
        self.assertAlmostEqual(self.so.zc_budget_total, 97 * 2100 + 1000 * 0.17 * 130)
        sub2 = self._approve_shop_drawing(self.sol_light, 105)
        self.assertTrue(sub2.activity_ids, "a shop drawing above the BOQ asks for a CRVO")

    def test_purchase_check_and_pm_exception(self):
        self._approve_shop_drawing(self.sol_light, 97)
        po = self._po(self.led, 100, 2150, boq=self.sol_light)
        line = po.order_line
        self.assertEqual(line.zc_check, "over_both")
        self.assertEqual(line.zc_allowed_remaining, 97)
        self.assertTrue(po.zc_needs_exception)
        with self.assertRaises(UserError):
            po.button_confirm()
        with self.assertRaises(UserError):
            po.action_zc_request_exception()  # no reason
        po.zc_exception_reason = "Breakage allowance"
        po.action_zc_request_exception()
        self.assertEqual(po.zc_exception_state, "requested")
        self.assertIn(self.pm, po.activity_ids.user_id, "the project manager gets the approval activity")
        buyer = self.env["res.users"].create({"name": "Buyer", "login": "zc_buyer", "group_ids": [
            Command.set([self.env.ref("purchase.group_purchase_user").id])]})
        with self.assertRaises(UserError):
            po.with_user(buyer).action_zc_approve_exception()
        po.with_user(self.pm).action_zc_approve_exception()
        self.assertEqual(po.zc_exception_user_id, self.pm)
        po.button_confirm()
        self.assertEqual(po.state, "purchase")
        po2 = self._po(self.led, 5, 2000, boq=self.sol_light)
        self.assertEqual(po2.order_line.zc_check, "over_qty", "nothing left after 100 of 97 were bought")
        po3 = self._po(self.paint, 170, 130)  # linked automatically from the project analytic
        self.assertEqual(po3.order_line.zc_boq_line_id, self.sol_paint)
        self.assertEqual(po3.order_line.zc_check, "ok")
        po3.button_confirm()

    def test_stock_elsewhere_hint(self):
        self._approve_shop_drawing(self.sol_light, 10)
        yard_po = self._po(self.led, 10, 2000, boq=self.sol_light, wh=self.yard)
        yard_po.button_confirm()
        self._receive(yard_po)
        po = self._po(self.led, 3, 2000, boq=self.sol_light)
        self.assertEqual(po.order_line.zc_check, "over_qty")
        self.assertIn(self.yard.name, str(po.zc_check_html), "the buyer sees the stock in the main store")

    def test_stage_valuation_and_subcontract(self):
        self._measure(self.sol_light, 50, "supplied", days_ago=10)
        self._measure(self.sol_light, 30, "installed", days_ago=9)
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id,
                                                            "period_end": self.today - timedelta(days=5)})
        cert.action_load_boq()
        line = cert.line_ids.filtered(lambda l: l.sale_line_id == self.sol_light)
        # 30 installed x 75% + 20 supplied-only x 60% = 34.5 units at the full rate
        self.assertAlmostEqual(line.cumulative_qty, 34.5)
        self.assertAlmostEqual(line.zc_installed_qty, 30)
        self.assertAlmostEqual(line.zc_supplied_only_qty, 20)
        self.assertAlmostEqual(cert.amount_current, 103500)
        cert.action_submit()
        cert.action_approve()
        self.assertAlmostEqual(self.sol_light.qty_delivered, 34.5)
        # subcontractor paid on the physical installed quantity
        sub = self.env["purchase.order"].create({
            "partner_id": self.vendor.id, "hom_is_subcontract": True,
            "order_line": [Command.create({"product_id": self.labour.id, "product_qty": 100, "price_unit": 100,
                                           "hom_boq_line_id": self.sol_light.id})]})
        sub.button_confirm()
        scert = self.env["hom.subcontract.certificate"].create({"purchase_order_id": sub.id})
        scert.action_load_items()
        self.assertAlmostEqual(scert.line_ids.cumulative_qty, 30)
        # the same 20 units are installed later: valued at 75% now
        self._measure(self.sol_light, 20, "installed", days_ago=2)
        cert2 = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id, "period_end": self.today})
        cert2.action_load_boq()
        line2 = cert2.line_ids.filtered(lambda l: l.sale_line_id == self.sol_light)
        self.assertAlmostEqual(line2.previous_qty, 34.5)
        self.assertAlmostEqual(line2.cumulative_qty, 37.5)
        self.assertAlmostEqual(line2.amount_current, 9000)

    def test_return_deadline_and_material_truth(self):
        self._approve_shop_drawing(self.sol_light, 97)
        po = self._po(self.led, 100, 2000, boq=self.sol_light, reason="Breakage allowance")
        po.action_zc_request_exception()
        po.action_zc_approve_exception()
        po.button_confirm()
        picking = self._receive(po, days_ago=10)
        self.assertEqual(picking.zc_return_deadline, picking.date_done.date() + timedelta(days=14))
        self._measure(self.sol_light, 95, "installed")
        self.env["zc.material.recon"].action_refresh()
        row = self.env["zc.material.recon"].search([("project_id", "=", self.project.id),
                                                    ("product_id", "=", self.led.id)])
        self.assertEqual(row.required_qty, 97)
        self.assertEqual(row.received_qty, 100)
        self.assertEqual(row.installed_qty, 95)
        # 5 not installed yet, the shop drawing still needs 2: 3 are surplus
        self.assertEqual(row.surplus_qty, 3)
        self.assertAlmostEqual(row.surplus_value, 6000)
        self.assertEqual(row.over_order_qty, 3)
        self.assertEqual(row.recommendation, "return")
        self.assertEqual(row.days_left, 4)
        self.env.company.write({"zc_return_alert_days": 5, "zc_stores_user_id": self.pm.id})
        self.env["stock.picking"]._zc_cron_return_alerts()
        self.assertTrue(picking.activity_ids.filtered(lambda a: a.user_id == self.pm))
        self.env["stock.picking"]._zc_cron_return_alerts()
        self.assertEqual(len(picking.activity_ids), 1, "no duplicate alert")
        data = self.env["hom.control.tower"].get_dashboard_data()
        self.assertTrue(any(a["level"] == "danger" and a["action"] == "zc_material_control.action_zc_material_recon_server"
                            for a in data["alerts"]), "return alert on the control tower (any language)")

    def test_interval_certificate(self):
        self.so.write({"hom_certificate_frequency": "interval", "zc_cert_interval_days": 20,
                       "date_order": fields.Datetime.now() - timedelta(days=25)})
        self._measure(self.sol_paint, 300, "installed", days_ago=3)
        Cert = self.env["hom.progress.certificate"]
        Cert._zc_cron_interval_drafts()
        cert = Cert.search([("sale_order_id", "=", self.so.id)])
        self.assertEqual(len(cert), 1)
        self.assertEqual(cert.period_end, self.today)
        Cert._zc_cron_interval_drafts()
        self.assertEqual(Cert.search_count([("sale_order_id", "=", self.so.id)]), 1)

    def test_post_init_books_retention_on_balance_sheet(self):
        retention = self.env.company.hom_retention_owner_product_id
        self.assertEqual(retention.product_tmpl_id.property_account_income_id, self.retention_account)
