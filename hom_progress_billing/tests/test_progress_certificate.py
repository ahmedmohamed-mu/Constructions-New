from odoo import Command
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestProgressCertificate(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("sales_team.group_sale_manager") | cls.env.ref("project.group_project_manager")
        cls.tax14 = cls.env["account.tax"].create({"name": "VAT 14", "amount": 14, "type_tax_use": "sale"})
        cls.retention_account = cls.company_data["default_account_receivable"].copy({
            "code": "102020", "name": "Retention receivable", "account_type": "asset_current", "reconcile": False})
        retention = cls.env.ref("hom_contracting_base.product_retention_owner")
        retention.property_account_income_id = cls.retention_account
        cls.env.company.hom_retention_owner_product_id = retention
        cls.dp_account = cls.company_data["default_account_revenue"].copy({
            "code": "201030", "name": "Customer advances", "account_type": "liability_current"})
        cls.env.company.downpayment_account_id = cls.dp_account
        boq = dict(type="service", service_type="manual", invoice_policy="delivery", taxes_id=[Command.set(cls.tax14.ids)])
        cls.item_a = cls.env["product.product"].create({"name": "FCU", **boq})
        cls.item_b = cls.env["product.product"].create({"name": "Pipes", **boq})
        cls.so = cls.env["sale.order"].create({
            "partner_id": cls.partner_a.id,
            "hom_advance_pct": 25, "hom_retention_pct": 5,
            "order_line": [
                Command.create({"product_id": cls.item_a.id, "product_uom_qty": 100, "price_unit": 1000}),
                Command.create({"product_id": cls.item_b.id, "product_uom_qty": 50, "price_unit": 2000}),
            ],
        })
        cls.so.action_confirm()
        wiz = cls.env["sale.advance.payment.inv"].with_context(
            active_model="sale.order", active_ids=cls.so.ids, active_id=cls.so.id).create(
            {"advance_payment_method": "percentage", "amount": 25})
        wiz.create_invoices()
        cls.advance_invoice = cls.so.invoice_ids
        cls.advance_invoice.action_post()

    def _certificate(self, qty_a, qty_b):
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id})
        cert.action_load_boq()
        for line in cert.line_ids:
            line.cumulative_qty = qty_a if line.product_id == self.item_a else qty_b
        return cert

    def test_full_cycle(self):
        self.assertAlmostEqual(self.advance_invoice.amount_untaxed, 50000)
        cert1 = self._certificate(30, 10)
        self.assertAlmostEqual(cert1.amount_current, 50000)
        self.assertAlmostEqual(cert1.amount_advance_recovery, -12500)
        self.assertAlmostEqual(cert1.amount_retention, -2500)
        cert1.action_submit()
        self.assertEqual(cert1.number, 1)
        cert1.action_approve()
        sol_a = self.so.order_line.filtered(lambda l: l.product_id == self.item_a)
        self.assertEqual(sol_a.qty_delivered, 30, "approval must push measured qty to the contract")
        cert1.action_create_invoice()
        inv1 = cert1.invoice_id
        self.assertEqual(cert1.state, "invoiced")
        self.assertEqual(inv1.move_type, "out_invoice")
        self.assertAlmostEqual(inv1.amount_untaxed, 35000)
        self.assertAlmostEqual(inv1.amount_tax, 5250)
        self.assertAlmostEqual(inv1.amount_total, 40250)
        ret_line = inv1.invoice_line_ids.filtered(lambda l: l.product_id == self.env.company.hom_retention_owner_product_id)
        self.assertEqual(ret_line.account_id, self.retention_account)
        dp_line = inv1.invoice_line_ids.filtered("is_downpayment")
        self.assertEqual(dp_line.account_id, self.dp_account)
        inv1.action_post()
        self.assertEqual(sol_a.qty_invoiced, 30)

        cert2 = self._certificate(100, 50)
        line_a = cert2.line_ids.filtered(lambda l: l.product_id == self.item_a)
        self.assertEqual(line_a.previous_qty, 30)
        self.assertEqual(line_a.current_qty, 70)
        self.assertAlmostEqual(cert2.amount_current, 150000)
        self.assertAlmostEqual(cert2.amount_advance_recovery, -37500)
        cert2.action_submit()
        self.assertEqual(cert2.number, 2)
        cert2.action_approve()
        cert2.action_create_invoice()
        cert2.invoice_id.action_post()
        self.assertAlmostEqual(cert2.invoice_id.amount_untaxed, 105000)
        self.assertAlmostEqual(self.env["hom.progress.certificate"].new({"sale_order_id": self.so.id})._remaining_advance(), 0.0)
        self.assertEqual(self.so.invoice_status, "invoiced")

    def test_cannot_exceed_contract(self):
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id})
        cert.action_load_boq()
        with self.assertRaises(Exception):
            cert.line_ids[0].cumulative_qty = 101
            cert.line_ids[0].flush_recordset()
