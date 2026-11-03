from datetime import timedelta

from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init


@tagged("post_install", "-at_install")
class TestContractAutomation(AccountTestInvoicingCommon):

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
        cls.tile = env["product.product"].create({"name": "Ceramic tile", "type": "consu", "is_storable": True,
                                                  "standard_price": 260})
        cls.item = env["product.product"].create({
            "name": "FN-01 Ceramic floor", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only", "taxes_id": [Command.clear()],
            "zc_cost_template_ids": [
                Command.create({"cost_type": "material", "product_id": cls.tile.id, "qty_per_unit": 1.05,
                                "unit_cost": 260}),
                Command.create({"cost_type": "labour", "name": "Installation", "qty_per_unit": 1, "unit_cost": 70})]})

    def _quotation(self, qty=400):
        return self.env["sale.order"].create({
            "partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 5,
            "order_line": [Command.create({"product_id": self.item.id, "product_uom_qty": qty, "price_unit": 0,
                                           "tax_ids": [Command.clear()]})]})

    def test_price_analysis_markup_site_store_and_purchase_project(self):
        env = self.env
        so = self._quotation()
        line = so.order_line
        # the standard price analysis of the item arrives with the line
        self.assertEqual(len(line.zc_cost_line_ids), 2)
        self.assertAlmostEqual(line.zc_unit_cost, 343)
        self.assertAlmostEqual(so.zc_budget_total, 137200)
        # selling price from the markup
        so.zc_markup_pct = 31.2
        so.action_zc_apply_markup()
        self.assertAlmostEqual(line.price_unit, 450.02, places=2)
        so.action_confirm()
        project = so.project_ids[:1]
        if not project.account_id:
            plan = env["account.analytic.plan"].search([], limit=1)
            project.account_id = env["account.analytic.account"].create({"name": "B7", "plan_id": plan.id})
        today = fields.Date.context_today(env.user)
        project.write({"date_start": today, "date": today + timedelta(days=90)})
        # the handover creates the site store of the project
        self.assertFalse(project.zc_warehouse_id)
        so.action_zc_handover_to_execution()
        store = project.zc_warehouse_id
        self.assertTrue(store, "the handover creates the site store")
        # a purchase received on the site store carries the project and finds its BOQ item by itself
        po = env["purchase.order"].create({
            "partner_id": self.vendor.id, "picking_type_id": store.in_type_id.id,
            "order_line": [Command.create({"product_id": self.tile.id, "product_qty": 420, "price_unit": 260})]})
        pol = po.order_line
        self.assertEqual(pol.analytic_distribution, {str(project.account_id.id): 100})
        self.assertEqual(pol.zc_boq_line_id, line)
        self.assertEqual(pol.zc_check, "ok")
        # the project shows the site records of its contract
        env["zc.daily.report"].create({"sale_order_id": so.id, "date": today,
                                       "labour_ids": [Command.create({"trade": "Tilers", "count": 4})]})
        project.invalidate_recordset()
        self.assertEqual(project.zc_daily_report_count, 1)
        self.assertEqual(project.action_zc_daily_reports()["domain"], [("sale_order_id", "=", so.id)])

    def test_save_standard_and_partner_country(self):
        so = self._quotation(10)
        so.order_line.zc_cost_line_ids.filtered(lambda c: c.cost_type == "labour").unit_cost = 80
        so.action_zc_save_cost_templates()
        labour = self.item.product_tmpl_id.zc_cost_template_ids.filtered(lambda t: t.cost_type == "labour")
        self.assertAlmostEqual(labour.unit_cost, 80)
        if self.env.company.country_id:
            partner = self.env["res.partner"].with_context(default_name="New owner").new({})
            defaults = self.env["res.partner"].default_get(["country_id"])
            self.assertEqual(defaults.get("country_id"), self.env.company.country_id.id)
