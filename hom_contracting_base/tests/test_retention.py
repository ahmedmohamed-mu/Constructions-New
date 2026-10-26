from odoo import Command
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestRetentionRelease(AccountTestInvoicingCommon):

    def test_owner_retention_release(self):
        self.env.user.group_ids |= self.env.ref("sales_team.group_sale_manager")
        ret_acc = self.company_data["default_account_receivable"].copy(
            {"code": "102020", "name": "Retention", "account_type": "asset_current", "reconcile": False})
        retention = self.env.ref("hom_contracting_base.product_retention_owner")
        retention.property_account_income_id = ret_acc
        self.env.company.hom_retention_owner_product_id = retention
        item = self.env["product.product"].create({"name": "Work", "type": "service", "invoice_policy": "order"})
        so = self.env["sale.order"].create({"partner_id": self.partner_a.id,
                                            "order_line": [Command.create({"product_id": item.id, "price_unit": 1000})]})
        so.action_confirm()
        inv = so._create_invoices()
        inv.invoice_line_ids = [Command.create({"product_id": retention.id, "quantity": 1, "price_unit": -50, "tax_ids": [Command.clear()]})]
        inv.action_post()
        self.assertAlmostEqual(so.hom_retention_balance, 50)
        action = so.action_hom_release_retention()
        release = self.env["account.move"].browse(action["res_id"])
        self.assertEqual(release.invoice_line_ids.account_id, ret_acc)
        release.action_post()
        so.invalidate_recordset()
        self.assertAlmostEqual(so.hom_retention_balance, 0)

    def test_fm_order_has_no_advance_or_retention(self):
        self.env.user.group_ids |= self.env.ref("sales_team.group_sale_manager")
        so = self.env["sale.order"].create({"partner_id": self.partner_a.id, "hom_project_type": "fm"})
        self.assertEqual((so.hom_advance_pct, so.hom_retention_pct), (0.0, 0.0))
        contract = self.env["sale.order"].create({"partner_id": self.partner_a.id})
        self.assertEqual(contract.hom_project_type, "contracting")
        self.assertEqual((contract.hom_advance_pct, contract.hom_retention_pct), (25.0, 5.0))
        if "plan_id" in self.env["sale.order"]._fields:
            plan = self.env["sale.subscription.plan"].search([], limit=1) or self.env["sale.subscription.plan"].create(
                {"name": "Monthly", "billing_period_value": 1, "billing_period_unit": "month"})
            sub = self.env["sale.order"].create({"partner_id": self.partner_a.id, "plan_id": plan.id})
            self.assertEqual(sub.hom_project_type, "fm")

    def test_no_project_analytic_on_advance_and_retention(self):
        ret_acc = self.company_data["default_account_receivable"].copy(
            {"code": "102029", "name": "Retention QA", "account_type": "asset_current", "reconcile": False})
        retention = self.env.ref("hom_contracting_base.product_retention_owner")
        retention.property_account_income_id = ret_acc
        self.env.company.hom_retention_owner_product_id = retention
        plan = self.env["account.analytic.plan"].create({"name": "Projects QA"})
        project_acc = self.env["account.analytic.account"].create({"name": "Site QA", "plan_id": plan.id})
        move = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.partner_a.id,
            "invoice_line_ids": [
                Command.create({"name": "Work", "price_unit": 1000, "analytic_distribution": {str(project_acc.id): 100}}),
                Command.create({"product_id": retention.id, "quantity": 1, "price_unit": -50,
                                "analytic_distribution": {str(project_acc.id): 100}}),
            ]})
        work, ret = move.invoice_line_ids
        self.assertTrue(work.analytic_distribution)
        self.assertFalse(ret.analytic_distribution, "retention stays off the project P&L")
