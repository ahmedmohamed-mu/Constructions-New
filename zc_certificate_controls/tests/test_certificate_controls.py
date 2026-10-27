from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init
from odoo.addons.zc_project_controls.hooks import _zc_pc_post_init
from ..hooks import _zc_cc_post_init


@tagged("post_install", "-at_install")
class TestCertificateControls(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("analytic.group_analytic_accounting") \
            | env.ref("account.group_account_manager") | env.ref("stock.group_stock_manager")
        cls.tax_s = env["account.tax"].create({"name": "VAT 14 S", "amount": 14, "type_tax_use": "sale"})
        cls.tax_p = env["account.tax"].create({"name": "VAT 14 P", "amount": 14, "type_tax_use": "purchase"})
        company = env.company
        ref = lambda x: env.ref("hom_contracting_base.%s" % x)
        company.write({"hom_retention_owner_product_id": ref("product_retention_owner").id,
                       "hom_retention_sub_product_id": ref("product_retention_sub").id,
                       "hom_sub_advance_product_id": ref("product_sub_advance").id,
                       "hom_sub_advance_recovery_product_id": ref("product_sub_advance_recovery").id})
        _zc_post_init(env)
        _zc_pc_post_init(env)
        _zc_cc_post_init(env)
        cls.types = {t.code: t for t in env["zc.deduction.type"].search([("company_id", "=", company.id)])}
        cls.owner = env["res.partner"].create({"name": "Owner (demo)"})
        cls.sub = env["res.partner"].create({"name": "Finishing subcontractor", "supplier_rank": 1})

        def item(name):
            return env["product.product"].create({
                "name": name, "type": "service", "service_type": "manual", "invoice_policy": "delivery",
                "service_tracking": "project_only", "taxes_id": [Command.set(cls.tax_s.ids)]})
        cls.item_a, cls.item_b = item("A Gypsum ceiling"), item("B Paint")

    def setUp(self):
        super().setUp()
        self.so = self.env["sale.order"].create({
            "partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 5,
            "order_line": [Command.create({"product_id": self.item_a.id, "product_uom_qty": 100, "price_unit": 1000}),
                           Command.create({"product_id": self.item_b.id, "product_uom_qty": 50, "price_unit": 2000})]})
        self.so.action_confirm()
        self.project = self.so.project_ids[:1]
        if not self.project.account_id:
            plan = self.env["account.analytic.plan"].search([], limit=1)
            self.project.account_id = self.env["account.analytic.account"].create({"name": "P", "plan_id": plan.id})
        self.line_a = self.so.order_line.filtered(lambda l: l.product_id == self.item_a)
        self.line_b = self.so.order_line.filtered(lambda l: l.product_id == self.item_b)

    def _measure(self, line, qty):
        return self.env["hom.site.measurement"].create({"sale_line_id": line.id, "quantity": qty,
                                                        "location": "1st floor"})

    def _cert(self):
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id})
        cert.action_load_boq()
        return cert

    def _cl(self, cert, line):
        return cert.line_ids.filtered(lambda l: l.sale_line_id == line)

    def _wir(self, measurements):
        return self.env["zc.inspection.request"].browse(measurements.action_zc_request_inspection()["res_id"])

    # ------------------------------------------------------------------
    def test_wir_gate(self):
        self.env.company.zc_wir_required = True
        m_a, m_b = self._measure(self.line_a, 30), self._measure(self.line_b, 10)
        wir = self._wir(m_a)
        self.assertEqual(wir.state, "draft")
        wir.action_submit()
        self.assertTrue(wir.date_due)
        wir.action_approve()
        self.assertTrue(m_a.zc_inspection_ok)
        cert = self._cert()
        self.assertEqual(self._cl(cert, self.line_a).cumulative_qty, 30)
        self.assertEqual(self._cl(cert, self.line_b).cumulative_qty, 0, "uninspected work stays out")
        self.assertEqual(cert.zc_wir_blocked, 1)
        self.assertFalse(m_b.certificate_id)
        alerts = self.env["hom.control.tower"]._alerts(self.env.company, self.so.date_order.date())
        self.assertTrue(any(a["action"].get("res_model") == "hom.site.measurement" for a in alerts
                            if isinstance(a["action"], dict)))

        wir_b = self._wir(m_b)
        wir_b.action_submit()
        wir_b.consultant_comments = "Joints not taped"
        wir_b.action_reject()
        self.assertFalse(m_b.zc_inspection_ok)
        new = self.env["zc.inspection.request"].browse(wir_b.action_reinspect()["res_id"])
        self.assertEqual(m_b.zc_wir_id, new)
        self.assertEqual(new.previous_id, wir_b)
        new.action_submit()
        new.action_approve_comments()
        cert.action_load_measurements()
        self.assertEqual(self._cl(cert, self.line_b).cumulative_qty, 10)
        cert.invalidate_recordset(["zc_wir_blocked"])
        self.assertEqual(cert.zc_wir_blocked, 0)

    def test_consultant_cut_and_claim(self):
        self._measure(self.line_a, 30)
        self._measure(self.line_b, 10)
        cert1 = self._cert()
        cert1.action_submit()
        self.assertTrue(cert1.zc_submitted_date)
        la = self._cl(cert1, self.line_a)
        self.assertEqual(la.zc_submitted_qty, 30)
        la.write({"cumulative_qty": 25, "zc_cut_reason": "Zone C not finished"})
        with self.assertRaises(UserError):
            la.cumulative_qty = 31
        cert1.action_approve()
        self.assertAlmostEqual(cert1.amount_current, 45000)
        self.assertAlmostEqual(cert1.zc_amount_submitted, 50000)
        self.assertAlmostEqual(cert1.zc_amount_cut, 5000)
        self.assertEqual(self.line_a.qty_delivered, 25)
        claim = cert1.zc_dispute_ids
        self.assertEqual(len(claim), 1)
        self.assertEqual((claim.qty, claim.amount, claim.state), (5, 5000, "open"))

        self._measure(self.line_a, 10)
        cert2 = self._cert()
        la2 = self._cl(cert2, self.line_a)
        self.assertEqual(la2.cumulative_qty, 40, "previous 25 + new 10 + claimed 5")
        self.assertEqual(la2.zc_claimed_qty, 5)
        self.assertEqual(claim.state, "resubmitted")
        cert2.action_load_measurements()
        self.assertEqual(la2.cumulative_qty, 40, "pulling again does not double the claim")
        cert2.action_submit()
        la2.cumulative_qty = 37
        cert2.action_approve()
        self.assertEqual(claim.state, "closed")
        self.assertEqual(claim.recovered_qty, 2)
        new_claim = cert2.zc_dispute_ids
        self.assertEqual(new_claim.qty, 3)

    def test_owner_deductions_and_withholding(self):
        self.so.zc_deduction_type_ids = self.types["WHT"]
        self._measure(self.line_a, 30)
        self._measure(self.line_b, 10)
        cert = self._cert()
        wht = cert.zc_deduction_ids
        self.assertEqual((wht.type_id, wht.pct, wht.amount), (self.types["WHT"], 1.0, 500))
        self.env["zc.certificate.deduction"].create({"owner_cert_id": cert.id, "type_id": self.types["PEN"].id,
                                                     "amount": 2000, "reference": "Penalty letter 12"})
        self.assertAlmostEqual(cert.zc_amount_payable, 50000 - 2500 - 2000)
        self.assertAlmostEqual(cert.zc_amount_withholding, -500)
        cert.action_submit()
        cert.action_approve()
        cert.action_create_invoice()
        inv = cert.invoice_id
        self.assertAlmostEqual(inv.amount_untaxed, 45500)
        self.assertAlmostEqual(inv.amount_tax, 7000)
        pen = inv.invoice_line_ids.filtered(lambda l: l.account_id == self.types["PEN"].account_id)
        self.assertAlmostEqual(pen.balance, 2000)
        self.assertEqual(pen.analytic_distribution, {str(self.project.account_id.id): 100})
        inv.action_post()
        self.assertTrue(cert.zc_withholding_open)
        self.assertAlmostEqual(cert.zc_expected_cash, 52000)
        cert.action_zc_settle_withholdings()
        self.assertAlmostEqual(inv.amount_residual, 52000)
        self.assertTrue(wht.settled_move_id)
        cert.invalidate_recordset(["zc_withholding_open"])
        self.assertFalse(cert.zc_withholding_open)
        rec = self.env["account.move.line"].search([("account_id", "=", self.types["WHT"].account_id.id),
                                                    ("parent_state", "=", "posted")])
        self.assertAlmostEqual(sum(rec.mapped("balance")), 500)

    def test_subcontract_chain_backcharges(self):
        company = self.env.company
        company.write({"zc_sub_multi_approval": True, "zc_qs_user_id": self.env.user.id})
        svc = self.env["product.product"].create({"name": "Gypsum install", "type": "service", "purchase_method": "receive",
                                                  "supplier_taxes_id": [Command.set(self.tax_p.ids)]})
        po = self.env["purchase.order"].create({
            "partner_id": self.sub.id, "hom_is_subcontract": True, "hom_retention_pct": 5,
            "zc_deduction_type_ids": [Command.set(self.types["SWHT"].ids)],
            "order_line": [Command.create({"product_id": svc.id, "product_qty": 100, "price_unit": 2800})]})
        po.button_confirm()
        # cement issued from our stores to the subcontractor
        cement = self.env["product.product"].create({"name": "Cement", "type": "consu", "is_storable": True,
                                                     "standard_price": 100})
        wh = self.env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        self.env["stock.quant"]._update_available_quantity(cement, wh.lot_stock_id, 50)
        picking = self.env["stock.picking"].create({
            "picking_type_id": wh.out_type_id.id, "partner_id": self.sub.id, "zc_charge_partner_id": self.sub.id,
            "move_ids": [Command.create({"product_id": cement.id, "product_uom_qty": 10,
                                         "location_id": wh.lot_stock_id.id,
                                         "location_dest_id": self.env.ref("stock.stock_location_customers").id})]})
        picking.action_confirm()
        picking.move_ids.quantity = 10
        picking.button_validate()
        self.assertEqual(picking.state, "done")
        self.assertAlmostEqual(picking.zc_charge_amount, 1000)

        cert = self.env["hom.subcontract.certificate"].create({"purchase_order_id": po.id})
        cert.action_load_items()
        cert.line_ids.cumulative_qty = 30
        cert.action_zc_load_materials()
        mat = cert.zc_deduction_ids.filtered(lambda d: d.type_id.code == "MAT")
        self.assertAlmostEqual(mat.amount, 1000)
        self.assertEqual(picking.zc_charge_deduction_id, mat)
        with self.assertRaises(UserError):
            cert.action_zc_load_materials()
        self.env["zc.certificate.deduction"].create({"sub_cert_id": cert.id, "type_id": self.types["BCK"].id,
                                                     "amount": 500})
        self.assertAlmostEqual(cert.zc_amount_payable, 84000 - 4200 - 1500)
        cert.action_submit()
        self.assertEqual(cert.zc_review_stage, "qs")
        with self.assertRaises(UserError):
            cert.action_approve()
        cert.action_zc_qs_check()
        self.assertEqual(cert.zc_review_stage, "pm")
        cert.action_approve()
        self.assertEqual((cert.state, cert.zc_review_stage), ("approved", "done"))
        cert.action_create_bill()
        bill = cert.bill_id
        self.assertAlmostEqual(bill.amount_untaxed, 78300)
        self.assertAlmostEqual(bill.amount_tax, 11760)
        back = bill.invoice_line_ids.filtered(lambda l: l.account_id == self.types["MAT"].account_id)
        self.assertAlmostEqual(sum(back.mapped("balance")), -1500)
        bill.invoice_date = bill.invoice_date or self.so.date_order.date()
        bill.action_post()
        cert.action_zc_settle_withholdings()
        self.assertAlmostEqual(bill.amount_residual, 90060 - 840)
        payable = self.env["account.move.line"].search([("account_id", "=", self.types["SWHT"].account_id.id),
                                                        ("parent_state", "=", "posted")])
        self.assertAlmostEqual(sum(payable.mapped("balance")), -840)

    def test_back_to_back_on_linked_items(self):
        """A trade subcontract is compared with the owner's certified quantity of its own BOQ item."""
        self._measure(self.line_a, 60)
        owner = self._cert()
        owner.action_submit()
        owner.action_approve()
        self.assertEqual(self.line_a.qty_delivered, 60)
        svc = self.env["product.product"].create({"name": "Gypsum labour", "type": "service", "purchase_method": "receive"})
        po = self.env["purchase.order"].create({
            "partner_id": self.sub.id, "hom_is_subcontract": True,
            "order_line": [Command.create({"product_id": svc.id, "product_qty": 100, "price_unit": 300,
                                           "hom_boq_line_id": self.line_a.id})]})
        po.button_confirm()
        cert = self.env["hom.subcontract.certificate"].create({"purchase_order_id": po.id})
        cert.action_load_items()
        self.assertEqual(cert.line_ids.cumulative_qty, 60)
        self.assertAlmostEqual(cert.owner_progress_pct, 60)
        self.assertFalse(cert.back_to_back_warning, "60% of the item certified on both sides")
        cert.line_ids.cumulative_qty = 80
        self.assertTrue(cert.back_to_back_warning)
