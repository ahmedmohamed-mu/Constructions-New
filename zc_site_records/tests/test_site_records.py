from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tools import mute_logger
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init
from odoo.addons.zc_project_controls.hooks import _zc_pc_post_init
from odoo.addons.zc_certificate_controls.hooks import _zc_cc_post_init


@tagged("post_install", "-at_install")
class TestSiteRecords(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("analytic.group_analytic_accounting") \
            | env.ref("account.group_account_manager")
        company = env.company
        ref = lambda x: env.ref("hom_contracting_base.%s" % x)
        company.write({"hom_retention_owner_product_id": ref("product_retention_owner").id,
                       "hom_retention_sub_product_id": ref("product_retention_sub").id,
                       "hom_sub_advance_product_id": ref("product_sub_advance").id,
                       "hom_sub_advance_recovery_product_id": ref("product_sub_advance_recovery").id})
        _zc_post_init(env)
        _zc_pc_post_init(env)
        _zc_cc_post_init(env)
        cls.owner = env["res.partner"].create({"name": "Owner (demo)"})
        cls.sub = env["res.partner"].create({"name": "Tiling crew", "supplier_rank": 1})
        cls.item = env["product.product"].create({
            "name": "FN-01 Ceramic floor", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only"})
        cls.so = env["sale.order"].create({
            "partner_id": cls.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 0,
            "order_line": [Command.create({"product_id": cls.item.id, "product_uom_qty": 100, "price_unit": 450})]})
        cls.so.action_confirm()
        cls.sol = cls.so.order_line

    def test_daily_report_feeds_measurements_and_claims(self):
        today = fields.Date.context_today(self.env.user)
        report = self.env["zc.daily.report"].create({
            "sale_order_id": self.so.id, "date": today,
            "work_ids": [Command.create({"sale_line_id": self.sol.id, "quantity": 40, "location": "GF"})],
            "labour_ids": [Command.create({"trade": "Tiler", "count": 6}),
                           Command.create({"trade": "Helper", "count": 4})],
            "delays": "Lobby area not handed over by the owner\nCrew moved to the first floor",
            "delay_hours": 4, "delay_cause": "site"})
        self.assertEqual(report.labour_total, 10)
        report.action_submit()
        self.assertEqual(report.state, "submitted")
        self.assertEqual(report.measurement_count, 1)
        measurement = report.work_ids.measurement_id
        self.assertEqual((measurement.sale_line_id, measurement.quantity, measurement.date), (self.sol, 40, today))
        report.action_submit()  # submitting again never duplicates the measurement
        self.assertEqual(len(self.env["hom.site.measurement"].search([("sale_line_id", "=", self.sol.id)])), 1)
        report.action_approve()
        self.assertEqual(report.state, "approved")
        action = report.action_open_claim()
        claim = self.env["zc.claim"].browse(action["res_id"])
        self.assertEqual((claim.event_date, claim.cause, claim.sale_order_id), (today, "site", self.so))
        self.assertEqual(claim.title, "Lobby area not handed over by the owner")
        with mute_logger("odoo.sql_db"), self.assertRaises(Exception):
            self.env["zc.daily.report"].create({"sale_order_id": self.so.id, "date": today})

    def test_rfi_deadline_and_follow_up(self):
        self.env.company.zc_rfi_answer_days = 7
        old = fields.Date.context_today(self.env.user) - timedelta(days=10)
        rfi = self.env["zc.rfi"].create({"subject": "Floor level at lobby", "sale_order_id": self.so.id,
                                         "date": old, "question": "<p>Which level?</p>", "impact": "both"})
        self.assertEqual(rfi.due_date, old + timedelta(days=7))
        rfi.action_send()
        self.assertTrue(rfi.overdue)
        self.assertEqual(rfi.days_late, 3)
        self.assertIn(rfi, self.env["zc.rfi"].search([("overdue", "=", True)]))
        alerts = self.env["hom.control.tower"]._alerts(self.env.company, fields.Date.context_today(self.env.user))
        self.assertTrue(any(a.get("action", {}).get("res_model") == "zc.rfi" for a in alerts
                            if isinstance(a.get("action"), dict)))
        with self.assertRaises(UserError):
            rfi.action_answer()
        rfi.answer = "<p>Raise the lobby 5 cm: added screed.</p>"
        rfi.action_answer()
        self.assertFalse(rfi.overdue)
        vo = self.env["hom.variation.order"].browse(rfi.action_create_variation()["res_id"])
        self.assertEqual(vo.sale_order_id, self.so)
        claim = self.env["zc.claim"].browse(rfi.action_create_claim()["res_id"])
        self.assertEqual(claim.claim_type, "both")

    def test_ncr_backcharge_to_subcontractor(self):
        svc = self.env["product.product"].create({"name": "Tile laying", "type": "service", "purchase_method": "receive"})
        po = self.env["purchase.order"].create({
            "partner_id": self.sub.id, "hom_is_subcontract": True,
            "order_line": [Command.create({"product_id": svc.id, "product_qty": 100, "price_unit": 80})]})
        po.button_confirm()
        cert = self.env["hom.subcontract.certificate"].create({"purchase_order_id": po.id})
        ncr = self.env["zc.ncr"].create({"title": "Wrong slope at corridor", "sale_order_id": self.so.id,
                                         "sale_line_id": self.sol.id, "description": "<p>Ponding water</p>",
                                         "responsible": "subcontractor", "subcontract_id": po.id,
                                         "partner_id": self.sub.id, "backcharge_amount": 1500})
        with self.assertRaises(UserError):
            ncr.action_verify()
        with self.assertRaises(UserError):
            ncr.action_done()
        ncr.corrective_action = "Tiles lifted and relaid with screed correction"
        ncr.action_done()
        ncr.action_verify()
        self.assertEqual(ncr.state, "closed")
        self.assertEqual(ncr.deduction_id.sub_cert_id, cert)
        self.assertAlmostEqual(ncr.deduction_id.amount, 1500)
        self.assertEqual(ncr.deduction_id.type_id.code, "BCK")
