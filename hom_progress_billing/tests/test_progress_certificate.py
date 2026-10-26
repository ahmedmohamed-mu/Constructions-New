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
        self.so.invalidate_recordset()
        self.assertAlmostEqual(self.so.hom_certified_amount, 50000)
        self.assertAlmostEqual(self.so.hom_advance_received, 50000)
        self.assertAlmostEqual(self.so.hom_advance_outstanding, 37500)

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
        # changing the contract terms later never rewrites certificates already in accounting
        self.so.invalidate_recordset()
        held = self.so.hom_retention_balance
        self.so.write({"hom_retention_pct": 10.0, "hom_advance_pct": 30.0})
        self.env.flush_all()
        cert1.invalidate_recordset()
        self.assertAlmostEqual(cert1.retention_pct, 5.0)
        self.assertAlmostEqual(cert1.amount_retention, -2500)
        self.assertAlmostEqual(cert1.amount_advance_recovery, -12500)
        self.so.invalidate_recordset()
        self.assertAlmostEqual(self.so.hom_retention_balance, held)
        cert3 = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id})
        self.assertAlmostEqual(cert3.retention_pct, 10.0, msg="new certificates follow the new terms")

    def test_cannot_exceed_contract(self):
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id})
        cert.action_load_boq()
        with self.assertRaises(Exception):
            cert.line_ids[0].cumulative_qty = 101
            cert.line_ids[0].flush_recordset()

    def test_personas_and_report(self):
        from odoo.tests import new_test_user
        engineer = new_test_user(self.env, "site.eng", groups="project.group_project_user")
        pm = new_test_user(self.env, "pm.user", groups="project.group_project_manager")
        accountant = new_test_user(self.env, "acc.user", groups="account.group_account_invoice")
        Cert = self.env["hom.progress.certificate"]
        cert = Cert.with_user(engineer).create({"sale_order_id": self.so.id})
        cert.action_load_boq()
        cert.line_ids[0].cumulative_qty = 10
        cert.action_submit()
        with self.assertRaises(Exception):
            cert.action_approve()
        cert.with_user(pm).action_approve()
        self.assertEqual(cert.state, "approved")
        cert.with_user(accountant).action_create_invoice()
        self.assertEqual(cert.state, "invoiced")
        self.assertTrue(cert.invoice_id)
        html = self.env["ir.actions.report"]._render_qweb_html(
            "hom_progress_billing.report_progress_certificate", cert.ids)[0]
        self.assertIn(b"10.0", html)

    def test_site_measurements_feed_certificate(self):
        sol_a = self.so.order_line.filtered(lambda l: l.product_id == self.item_a)
        M = self.env["hom.site.measurement"]
        m1 = M.create({"sale_line_id": sol_a.id, "quantity": 12, "date": "2026-11-10"})
        M.create({"sale_line_id": sol_a.id, "quantity": 8, "date": "2026-11-20"})
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id})
        cert.action_load_boq()
        line_a = cert.line_ids.filtered(lambda l: l.sale_line_id == sol_a)
        self.assertEqual(line_a.cumulative_qty, 20)
        self.assertEqual(m1.state, "in_certificate")
        cert.action_submit()
        cert.action_approve()
        self.assertEqual(m1.state, "certified")
        with self.assertRaises(Exception):
            m1.quantity = 1
        # next certificate only takes new measurements
        M.create({"sale_line_id": sol_a.id, "quantity": 5})
        cert2 = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id})
        cert2.action_load_boq()
        self.assertEqual(cert2.line_ids.filtered(lambda l: l.sale_line_id == sol_a).cumulative_qty, 25)

    def test_monthly_cron_and_weekly_update(self):
        from datetime import date
        self.env.company.hom_certificate_day = date.today().day
        Cert = self.env["hom.progress.certificate"]
        before = Cert.search_count([("sale_order_id", "=", self.so.id)])
        Cert._cron_monthly_drafts()
        self.assertEqual(Cert.search_count([("sale_order_id", "=", self.so.id)]), before, "no site work, no draft")
        self.env["hom.site.measurement"].create({"sale_line_id": self.so.order_line.filtered(lambda l: l.product_id == self.item_a).id,
                                                 "quantity": 5, "date": date.today()})
        Cert._cron_monthly_drafts()
        self.assertEqual(Cert.search_count([("sale_order_id", "=", self.so.id)]), before + 1)
        Cert._cron_monthly_drafts()  # no duplicate while a draft exists
        self.assertEqual(Cert.search_count([("sale_order_id", "=", self.so.id)]), before + 1)
        project = self.env["project.project"].create({"name": "P", "sale_line_id": self.so.order_line[0].id,
                                                      "hom_estimate_to_complete": 100000})
        project.account_id = self.env["account.analytic.account"].create(
            {"name": "P", "plan_id": self.env["account.analytic.plan"].search([], limit=1).id})
        self.env["project.project"]._cron_weekly_update()
        update = self.env["project.update"].search([("project_id", "=", project.id)])
        self.assertTrue(update)
        self.assertEqual(update.status, "on_track")

    def test_certificate_frequency(self):
        from datetime import date
        Cert = self.env["hom.progress.certificate"]
        sol = self.so.order_line.filtered(lambda l: l.product_id == self.item_a)
        self.env["hom.site.measurement"].create({"sale_line_id": sol.id, "quantity": 5, "date": date.today()})
        self.env.company.hom_certificate_day = date.today().day
        # on demand: never automatic
        self.so.hom_certificate_frequency = "on_demand"
        Cert._cron_monthly_drafts()
        self.assertFalse(Cert.search([("sale_order_id", "=", self.so.id), ("state", "=", "draft")]))
        # milestone: draft when a milestone is reached, not on the monthly day
        self.so.hom_certificate_frequency = "milestone"
        Cert._cron_monthly_drafts()
        self.assertFalse(Cert.search([("sale_order_id", "=", self.so.id), ("state", "=", "draft")]))
        project = self.env["project.project"].create({"name": "MS", "sale_line_id": sol.id, "allow_milestones": True})
        milestone = self.env["project.milestone"].create({"name": "Chillers installed", "project_id": project.id})
        pm = self.env["res.users"].create({"name": "PM only", "login": "pm_only_hom",
                                           "group_ids": [Command.set([self.env.ref("project.group_project_manager").id])]})
        milestone.with_user(pm).is_reached = True
        draft = Cert.search([("sale_order_id", "=", self.so.id), ("state", "=", "draft")])
        self.assertEqual(len(draft), 1)
        self.assertEqual(draft.line_ids.filtered(lambda l: l.sale_line_id == sol).cumulative_qty, 5)
