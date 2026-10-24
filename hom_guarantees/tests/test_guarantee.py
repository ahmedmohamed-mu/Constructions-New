from datetime import date, timedelta

from odoo import Command
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestGuarantee(AccountTestInvoicingCommon):

    def test_advance_reduction_and_expiry(self):
        self.env.user.group_ids |= self.env.ref("sales_team.group_sale_manager")
        item = self.env["product.product"].create({"name": "W", "type": "service", "service_type": "manual"})
        so = self.env["sale.order"].create({"partner_id": self.partner_a.id,
                                            "order_line": [Command.create({"product_id": item.id, "product_uom_qty": 10, "price_unit": 1000})]})
        so.action_confirm()
        wiz = self.env["sale.advance.payment.inv"].with_context(active_model="sale.order", active_ids=so.ids).create(
            {"advance_payment_method": "percentage", "amount": 25})
        wiz.create_invoices()
        so.invoice_ids.action_post()
        g = self.env["hom.bank.guarantee"].create({
            "name": "LG-1", "partner_id": self.partner_a.id, "guarantee_type": "advance", "sale_order_id": so.id,
            "amount": 2500, "expiry_date": date.today() + timedelta(days=10)})
        g.action_refresh_amount()
        self.assertAlmostEqual(g.amount_current, 2500)
        self.env["hom.bank.guarantee"]._cron_check_expiry()
        self.assertTrue(g.activity_ids)
        g.expiry_date = date.today() - timedelta(days=1)
        self.env["hom.bank.guarantee"]._cron_check_expiry()
        self.assertEqual(g.state, "expired")
