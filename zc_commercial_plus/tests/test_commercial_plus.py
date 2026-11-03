from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init
from odoo.addons.zc_project_controls.hooks import _zc_pc_post_init
from odoo.addons.zc_certificate_controls.hooks import _zc_cc_post_init
from odoo.addons.zc_claims_escalation.hooks import _zc_ce_post_init


@tagged("post_install", "-at_install")
class TestCommercialPlus(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("account.group_account_manager") \
            | env.ref("analytic.group_analytic_accounting")
        company = env.company
        ref = lambda x: env.ref("hom_contracting_base.%s" % x)
        company.write({"hom_retention_owner_product_id": ref("product_retention_owner").id,
                       "hom_retention_sub_product_id": ref("product_retention_sub").id,
                       "hom_sub_advance_product_id": ref("product_sub_advance").id,
                       "hom_sub_advance_recovery_product_id": ref("product_sub_advance_recovery").id,
                       "zc_price_tolerance_pct": 0.0})
        _zc_post_init(env)
        _zc_pc_post_init(env)
        _zc_cc_post_init(env)
        _zc_ce_post_init(env)
        cls.owner = env["res.partner"].create({"name": "Owner"})
        cls.sub_a = env["res.partner"].create({"name": "Crew A", "supplier_rank": 1})
        cls.sub_b = env["res.partner"].create({"name": "Crew B", "supplier_rank": 1})
        cls.item = env["product.product"].create({
            "name": "FN-04 Gypsum ceiling", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only", "taxes_id": [Command.clear()]})
        cls.labour = env["product.product"].create({"name": "Ceiling labour", "type": "service", "purchase_ok": True,
                                                    "purchase_method": "purchase",
                                                    "supplier_taxes_id": [Command.clear()]})
        today = fields.Date.context_today(env.user)
        cls.so = env["sale.order"].create({
            "partner_id": cls.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 5,
            "order_line": [Command.create({"product_id": cls.item.id, "product_uom_qty": 100, "price_unit": 780,
                                           "tax_ids": [Command.clear()]})]})
        cls.so.action_confirm()
        cls.project = cls.so.project_ids[:1]
        if not cls.project.account_id:
            plan = env["account.analytic.plan"].search([], limit=1)
            cls.project.account_id = env["account.analytic.account"].create({"name": "P", "plan_id": plan.id})
        cls.project.write({"date_start": today, "date": today + timedelta(days=120)})
        env["zc.boq.cost.line"].create({"sale_line_id": cls.so.order_line.id, "cost_type": "labour",
                                        "name": "Install", "unit_cost": 150})

    def test_package_compare_and_award(self):
        pkg = self.env["zc.sub.package"].create({
            "title": "Gypsum ceilings labour", "sale_order_id": self.so.id, "product_id": self.labour.id,
            "sale_line_ids": [Command.set(self.so.order_line.ids)],
            "partner_ids": [Command.set((self.sub_a | self.sub_b).ids)]})
        self.assertAlmostEqual(pkg.budget_amount, 15000)
        pkg.action_request_offers()
        self.assertEqual(len(pkg.rfq_ids), 2)
        a = pkg.rfq_ids.filtered(lambda p: p.partner_id == self.sub_a)
        b = pkg.rfq_ids - a
        a.order_line.price_unit = 160
        b.order_line.price_unit = 140
        self.assertIn("Crew A", pkg.comparison_html)
        pkg.action_award()
        self.assertEqual(pkg.awarded_id, b)
        self.assertEqual(b.state, "purchase")
        self.assertEqual(a.state, "cancel")
        self.assertEqual(b.order_line.zc_check, "ok")

    def test_sub_claim_back_to_back(self):
        po = self.env["purchase.order"].create({
            "partner_id": self.sub_a.id, "hom_is_subcontract": True,
            "order_line": [Command.create({"product_id": self.labour.id, "product_qty": 100, "price_unit": 150,
                                           "hom_boq_line_id": self.so.order_line.id})]})
        po.button_confirm()
        with self.assertRaises(ValidationError):
            self.env["zc.claim"].create({"claimant": "sub", "sale_order_id": self.so.id, "title": "No PO"})
        claim = self.env["zc.claim"].create({
            "claimant": "sub", "subcontract_id": po.id, "sale_order_id": self.so.id, "title": "Ceiling grid late",
            "cause": "site", "claim_type": "both", "event_date": fields.Date.context_today(self.env.user),
            "days_claimed": 5, "amount_claimed": 4000})
        owner = self.env["zc.claim"].browse(claim.action_pass_to_owner()["res_id"])
        self.assertEqual((owner.claimant, owner.event_date, owner.amount_claimed), ("us", claim.event_date, 4000))
        self.assertEqual(owner.sub_claim_ids, claim)
        end = self.project.date
        claim.action_notify()
        claim.action_submit()
        claim.write({"days_granted": 5, "amount_granted": 3000})
        claim.action_partial()
        self.assertEqual(self.project.date, end, "time given to a subcontractor does not move our end date")
        claim.action_create_invoice()
        self.assertEqual((claim.bill_id.move_type, claim.bill_id.partner_id), ("in_invoice", self.sub_a))
        self.assertAlmostEqual(claim.bill_id.amount_untaxed, 3000)

    def test_sub_certificate_without_advance_matches_its_bill(self):
        po = self.env["purchase.order"].create({
            "partner_id": self.sub_b.id, "hom_is_subcontract": True, "hom_advance_recovery_pct": 10,
            "order_line": [Command.create({"product_id": self.labour.id, "product_qty": 100, "price_unit": 68,
                                           "hom_boq_line_id": self.so.order_line.id})]})
        po.button_confirm()
        self.env["hom.site.measurement"].create({"sale_line_id": self.so.order_line.id, "quantity": 40})
        cert = self.env["hom.subcontract.certificate"].create({"purchase_order_id": po.id})
        cert.action_load_items()
        self.assertAlmostEqual(cert.amount_current, 40 * 68)
        cert.action_submit()
        if cert.zc_review_stage == "qs":
            cert.action_zc_qs_check()
        cert.action_approve()
        cert.action_create_bill()
        self.assertAlmostEqual(cert.amount_advance_recovery, 0, msg="no advance paid, nothing to recover")
        self.assertAlmostEqual(cert.amount_net, cert.bill_id.amount_untaxed)

    def test_handover_documents_block_signature(self):
        ho = self.env["zc.handover"].create({"sale_order_id": self.so.id, "kind": "preliminary"})
        ho.action_load_documents()
        self.assertEqual(len(ho.document_ids), 6)
        self.assertEqual(ho.document_missing, 4)
        with self.assertRaises(UserError):
            ho.action_sign()
        ho.document_ids.filtered("required").write({"received": True})
        ho.action_sign()
        self.assertEqual(ho.state, "signed")

    def test_cashflow_and_safety(self):
        today = fields.Date.context_today(self.env.user)
        action = self.env["zc.cashflow.line"].action_refresh()
        self.assertEqual(action["res_model"], "zc.cashflow.line")
        rows = self.env["zc.cashflow.line"].search([("sale_order_id", "=", self.so.id)])
        self.assertTrue(rows.filtered(lambda r: r.kind == "in_future"))
        self.assertTrue(rows.filtered(lambda r: r.kind == "out_budget"))
        # the whole contract is collected net of retention, the retention comes back after the defects period
        future = sum(rows.filtered(lambda r: r.kind == "in_future").mapped("amount"))
        self.assertAlmostEqual(future, 78000 * 0.95, places=0)
        self.assertAlmostEqual(sum(rows.filtered(lambda r: r.kind == "in_retention").mapped("amount")), 3900, places=0)
        self.assertAlmostEqual(sum(rows.filtered(lambda r: r.kind == "out_budget").mapped("amount")), -15000, places=0)
        self.assertLess(self.env["zc.cashflow.line"]._zc_peak_need(self.so), 0, "the work is paid before collected")
        # safety: an injury needs a description and raises an alert
        with self.assertRaises(UserError):
            self.env["zc.daily.report"].create({"sale_order_id": self.so.id, "date": today, "incident": "lost_time"})
        self.env["zc.daily.report"].create({"sale_order_id": self.so.id, "date": today, "incident": "lost_time",
                                            "incident_note": "Fall from a ladder, worker sent to hospital"})
        alerts = self.env["hom.control.tower"]._alerts(self.env.company, today)
        self.assertTrue(any(a.get("action") and a["action"].get("res_model") == "zc.daily.report" for a in alerts
                            if isinstance(a.get("action"), dict)))
