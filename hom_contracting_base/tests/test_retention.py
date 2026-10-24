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
