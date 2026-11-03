from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init


@tagged("post_install", "-at_install")
class TestPerpetualValuation(AccountTestInvoicingCommon):
    """Materials valued perpetually: the bill puts them in stock, the project is charged when they are used."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("stock.group_stock_manager") \
            | env.ref("analytic.group_analytic_accounting") | env.ref("account.group_account_manager")
        _zc_post_init(env)
        company = env.company
        company._zc_setup_stock_valuation_accounts()
        if not company.account_stock_journal_id:
            company.account_stock_journal_id = env["account.journal"].create(
                {"name": "Stock valuation", "code": "ZSTK", "type": "general", "company_id": company.id})
        cls.stock_account = env["account.account"].create(
            {"code": "120199", "name": "Materials in stores", "account_type": "asset_current"})
        cls.material_cost = company._zc_consumption_location().valuation_account_id
        cls.categ = env["product.category"].create({"name": "Materials (perpetual)"})
        cls.categ.with_company(company).write({
            "property_valuation": "real_time", "property_cost_method": "average",
            "property_stock_valuation_account_id": cls.stock_account.id,
            "property_account_expense_categ_id": cls.material_cost.id})
        cls.owner = env["res.partner"].create({"name": "Owner"})
        cls.vendor = env["res.partner"].create({"name": "Tile vendor", "supplier_rank": 1})
        cls.item = env["product.product"].create({
            "name": "FN-01 Ceramic floor", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only"})
        cls.tile = env["product.product"].create({
            "name": "Ceramic tile", "type": "consu", "is_storable": True, "categ_id": cls.categ.id,
            "purchase_method": "receive", "supplier_taxes_id": [Command.clear()]})
        cls.site = env["stock.warehouse"].create({"name": "Site PV", "code": "SPV"})

    def test_bill_to_stock_issue_to_project(self):
        env = self.env
        self.assertEqual(self.tile.valuation, "real_time")
        self.assertEqual(self.material_cost.code, "400011")
        so = env["sale.order"].create({"partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 0,
                                       "order_line": [Command.create({"product_id": self.item.id,
                                                                      "product_uom_qty": 100, "price_unit": 450})]})
        so.action_confirm()
        project = so.project_ids[:1]
        if not project.account_id:
            plan = env["account.analytic.plan"].search([], limit=1)
            project.account_id = env["account.analytic.account"].create({"name": "PV", "plan_id": plan.id})
        project.zc_warehouse_id = self.site
        analytic = project.account_id
        sol = so.order_line
        env["zc.boq.cost.line"].create({"sale_line_id": sol.id, "product_id": self.tile.id, "qty_per_unit": 1.05,
                                        "unit_cost": 260})

        # buy 50 m2 for the project, received on site, billed
        po = env["purchase.order"].create({
            "partner_id": self.vendor.id, "picking_type_id": self.site.in_type_id.id,
            "order_line": [Command.create({"product_id": self.tile.id, "product_qty": 50, "price_unit": 260,
                                           "tax_ids": [Command.clear()],
                                           "analytic_distribution": {str(analytic.id): 100}})]})
        po.button_confirm()
        receipt = po.picking_ids
        receipt.move_ids.quantity = 50
        receipt.move_ids.picked = True
        receipt.button_validate()
        po.action_create_invoice()
        bill = po.invoice_ids
        bill.invoice_date = fields.Date.context_today(env.user)
        bill.action_post()
        line = bill.invoice_line_ids
        self.assertEqual(line.account_id, self.stock_account, "the bill puts the material in stock")
        self.assertEqual(line.analytic_distribution, {str(analytic.id): 100}, "the line still tells the project")
        cost = lambda: -sum(env["account.analytic.line"].search([(analytic.plan_id._column_name(), "=", analytic.id)]).mapped("amount"))
        self.assertAlmostEqual(cost(), 0, msg="material in the store is not a project cost yet")

        # 40 m2 laid: 42 m2 issued from the site store to the work
        env.company.zc_issue_on_measurement = True
        m = env["hom.site.measurement"].create({"sale_line_id": sol.id, "quantity": 40, "location": "GF"})
        picking = m.zc_issue_move_ids.picking_id
        picking.move_ids.quantity = 42
        picking.move_ids.picked = True
        picking.button_validate()
        entry = picking.move_ids.account_move_id
        self.assertEqual(entry.state, "posted")
        debit = entry.line_ids.filtered(lambda l: l.debit)
        credit = entry.line_ids.filtered(lambda l: l.credit)
        self.assertEqual((debit.account_id, credit.account_id), (self.material_cost, self.stock_account))
        self.assertAlmostEqual(debit.debit, 42 * 260)
        self.assertEqual(debit.analytic_distribution, {str(analytic.id): 100})
        self.assertAlmostEqual(cost(), 42 * 260, msg="the project is charged with what was used")

        stock_balance = sum(env["account.move.line"].search([("account_id", "=", self.stock_account.id),
                                                             ("parent_state", "=", "posted")]).mapped("balance"))
        self.assertAlmostEqual(stock_balance, 8 * 260, msg="the stock account holds what is left in the store")
        self.assertAlmostEqual(self.tile.with_context(warehouse_id=self.site.id).qty_available, 8)
