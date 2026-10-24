from odoo import Command
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestSubcontractCertificate(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.group_ids |= cls.env.ref("purchase.group_purchase_manager")
        cls.vat = cls.env["account.tax"].create({"name": "VAT 14 P", "amount": 14, "type_tax_use": "purchase"})
        company = cls.env.company
        adv_acc = cls.company_data["default_account_expense"].copy({"code": "102021", "name": "Advances to subcontractors", "account_type": "asset_current"})
        ret_acc = cls.company_data["default_account_expense"].copy({"code": "201031", "name": "Retention payable", "account_type": "liability_current"})
        adv = cls.env.ref("hom_contracting_base.product_sub_advance")
        rec = cls.env.ref("hom_contracting_base.product_sub_advance_recovery")
        ret = cls.env.ref("hom_contracting_base.product_retention_sub")
        adv.write({"property_account_expense_id": adv_acc.id, "supplier_taxes_id": [Command.set(cls.vat.ids)]})
        rec.write({"property_account_expense_id": adv_acc.id, "supplier_taxes_id": [Command.set(cls.vat.ids)]})
        ret.property_account_expense_id = ret_acc
        company.write({"hom_sub_advance_product_id": adv.id, "hom_sub_advance_recovery_product_id": rec.id,
                       "hom_retention_sub_product_id": ret.id})
        cls.adv_acc, cls.ret_acc = adv_acc, ret_acc
        svc = dict(type="service", purchase_method="receive", supplier_taxes_id=[Command.set(cls.vat.ids)])
        items = [("FCU install", 120, 2800), ("Pipe 8in", 180, 850), ("Pipe 4-6in", 400, 500), ("Pipe 1-3in", 1600, 230)]
        cls.products = [cls.env["product.product"].create({"name": n, **svc}) for n, _q, _p in items]
        cls.po = cls.env["purchase.order"].create({
            "partner_id": cls.partner_a.id, "hom_is_subcontract": True, "hom_advance_amount": 124230,
            "hom_advance_recovery_pct": 10, "hom_retention_pct": 5,
            "order_line": [Command.create({"product_id": p.id, "product_qty": q, "price_unit": pr})
                           for p, (_n, q, pr) in zip(cls.products, items)],
        })
        cls.po.button_confirm()

    def test_cycle(self):
        action = self.po.action_hom_advance_bill()
        adv_bill = self.env["account.move"].browse(action["res_id"])
        adv_bill.invoice_date = "2026-11-10"
        adv_bill.action_post()
        self.assertAlmostEqual(adv_bill.amount_total, 141622.2)
        self.assertAlmostEqual(self.po.hom_advance_remaining, 124230)

        cert = self.env["hom.subcontract.certificate"].create({"purchase_order_id": self.po.id, "date": "2026-12-07"})
        cert.action_load_items()
        for line, qty in zip(cert.line_ids.sorted("sequence"), [30, 90, 200, 600]):
            line.cumulative_qty = qty
        self.assertAlmostEqual(cert.amount_current, 398500)
        self.assertAlmostEqual(cert.amount_advance_recovery, -39850)
        self.assertAlmostEqual(cert.amount_retention, -19925)
        cert.action_submit()
        cert.action_approve()
        self.assertEqual(self.po.order_line[0].qty_received, 30)
        cert.action_create_bill()
        bill = cert.bill_id
        self.assertAlmostEqual(bill.amount_tax, 50211)
        self.assertAlmostEqual(bill.amount_total, 388936)
        bill.action_post()
        self.po.invalidate_recordset()
        self.assertAlmostEqual(self.po.hom_advance_remaining, 84380)
        ret_line = bill.invoice_line_ids.filtered(lambda l: l.product_id == self.env.company.hom_retention_sub_product_id)
        self.assertEqual(ret_line.account_id, self.ret_acc)
        self.assertEqual(self.po.order_line[0].qty_invoiced, 30)

    def test_report_and_site_engineer(self):
        from odoo.tests import new_test_user
        engineer = new_test_user(self.env, "site.eng2", groups="project.group_project_user")
        cert = self.env["hom.subcontract.certificate"].with_user(engineer).create({"purchase_order_id": self.po.id})
        cert.action_load_items()
        cert.line_ids[0].cumulative_qty = 5
        cert.action_submit()
        cert.sudo().action_approve()
        html = self.env["ir.actions.report"]._render_qweb_html(
            "hom_subcontract_billing.report_subcontract_certificate", cert.ids)[0]
        self.assertIn(b"5.00", html)

    def test_back_to_back_warning(self):
        analytic = self.env["account.analytic.account"].create(
            {"name": "Proj", "plan_id": self.env["account.analytic.plan"].search([], limit=1).id})
        project = self.env["project.project"].sudo().create({"name": "Proj", "account_id": analytic.id,
                                                      "user_id": self.env.user.id})
        self.po.order_line.analytic_distribution = {str(analytic.id): 100}
        cert = self.env["hom.subcontract.certificate"].create({"purchase_order_id": self.po.id})
        cert.action_load_items()
        cert.line_ids[0].cumulative_qty = 120
        self.assertEqual(cert.project_id, project)
        expected = "hom.progress.certificate" in self.env
        self.assertEqual(cert.back_to_back_warning, expected)
        cert.action_submit()
        if expected:
            self.assertTrue(cert.activity_ids)
