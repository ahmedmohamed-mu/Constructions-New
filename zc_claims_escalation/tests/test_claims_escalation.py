from datetime import date, timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init
from odoo.addons.zc_project_controls.hooks import _zc_pc_post_init
from odoo.addons.zc_certificate_controls.hooks import _zc_cc_post_init
from ..hooks import _zc_ce_post_init


@tagged("post_install", "-at_install")
class TestClaimsEscalation(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("account.group_account_manager")
        ref = lambda x: env.ref("hom_contracting_base.%s" % x)
        env.company.write({"hom_retention_owner_product_id": ref("product_retention_owner").id})
        _zc_post_init(env)
        _zc_pc_post_init(env)
        _zc_cc_post_init(env)
        _zc_ce_post_init(env)
        cls.owner = env["res.partner"].create({"name": "Owner"})
        cls.item = env["product.product"].create({"name": "Works", "type": "service", "service_type": "manual",
                                                  "invoice_policy": "delivery", "service_tracking": "project_only"})
        cls.so = env["sale.order"].create({
            "partner_id": cls.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 0,
            "order_line": [Command.create({"product_id": cls.item.id, "product_uom_qty": 100, "price_unit": 1000})]})
        cls.so.action_confirm()
        cls.project = cls.so.project_ids[:1]
        cls.project.write({"date_start": date(2026, 9, 1), "date": date(2027, 3, 31)})

    def test_price_escalation(self):
        Index = self.env["zc.price.index"]
        idx = {i.code: i for i in Index.search([])}
        for code, base, cur in (("STEEL", 100, 120), ("CEMENT", 100, 110), ("LABOUR", 100, 105)):
            idx[code].value_ids = [Command.create({"date": date(2026, 1, 1), "value": base}),
                                   Command.create({"date": date(2026, 6, 1), "value": cur})]
        self.so.write({"zc_escalation": True, "zc_escalation_base_date": date(2026, 1, 15),
                       "zc_escalation_formula_ids": [Command.create({"index_id": idx["STEEL"].id, "weight": 25}),
                                                     Command.create({"index_id": idx["CEMENT"].id, "weight": 15}),
                                                     Command.create({"index_id": idx["LABOUR"].id, "weight": 30})]})
        self.assertAlmostEqual(self.so.zc_escalation_fixed, 30)
        self.env["hom.site.measurement"].create({"sale_line_id": self.so.order_line.id, "quantity": 100,
                                                 "date": date(2026, 6, 10)})
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": self.so.id, "date": date(2026, 6, 30),
                                                            "period_end": date(2026, 6, 30)})
        cert.action_load_boq()
        cert.action_submit()
        cert.action_approve()
        esc = self.env["zc.escalation"].create({"sale_order_id": self.so.id, "date_from": date(2026, 6, 1),
                                                "date_to": date(2026, 6, 30)})
        esc.action_compute()
        self.assertAlmostEqual(esc.work_value, 100000)
        self.assertAlmostEqual(esc.factor, 0.08, places=6)
        self.assertAlmostEqual(esc.amount, 8000)
        self.assertAlmostEqual(sum(esc.line_ids.mapped("amount")), 8000)
        esc.action_submit()
        esc.action_approve()
        esc.action_create_invoice()
        self.assertEqual(esc.state, "invoiced")
        self.assertAlmostEqual(esc.invoice_id.amount_untaxed, 8000)
        self.assertEqual(esc.invoice_id.invoice_line_ids.product_id, self.env.company.zc_escalation_product_id)

    def test_claim_clock(self):
        today = fields.Date.context_today(self.env.user)
        old = self.env["zc.claim"].create({"sale_order_id": self.so.id, "title": "Late site access",
                                           "event_date": today - timedelta(days=35)})
        self.assertTrue(old.time_barred)
        soon = self.env["zc.claim"].create({"sale_order_id": self.so.id, "title": "Late drawings",
                                            "event_date": today - timedelta(days=20)})
        self.assertEqual(soon.notice_deadline, today + timedelta(days=8))
        alerts = self.env["hom.control.tower"]._alerts(self.env.company, today)
        self.assertTrue(any(isinstance(a.get("action"), dict) and a["action"].get("res_model") == "zc.claim"
                            and soon.id in a["action"]["domain"][0][2] for a in alerts))
        g = self.env["hom.bank.guarantee"].create({
            "name": "LG-1", "direction": "issued", "guarantee_type": "performance", "partner_id": self.owner.id,
            "sale_order_id": self.so.id, "amount": 5000, "expiry_date": date(2027, 4, 5)})
        soon.action_notify()
        self.assertFalse(soon.time_barred)
        with self.assertRaises(UserError):
            soon.action_submit()
        soon.write({"days_claimed": 20, "amount_claimed": 50000})
        soon.action_submit()
        self.assertEqual(soon.answer_deadline, soon.submission_date + timedelta(days=42))
        soon.write({"days_granted": 10, "amount_granted": 30000})
        soon.action_partial()
        self.assertEqual(self.project.date, date(2027, 4, 10))
        self.assertTrue(soon.eot_applied)
        self.assertTrue(g.activity_ids, "guarantee expiring before the extended completion")
        self.assertEqual(soon.nod_deadline, soon.determination_date + timedelta(days=28))
        soon.action_create_invoice()
        self.assertAlmostEqual(soon.invoice_id.amount_untaxed, 30000)
        soon.action_dispute()
        self.assertEqual(soon.state, "dispute")

    def test_eot_needs_a_completion_date(self):
        """A new contract whose project has no planned dates cannot silently swallow an extension of time."""
        today = fields.Date.context_today(self.env.user)
        claim = self.env["zc.claim"].create({"sale_order_id": self.so.id, "title": "Late access", "claim_type": "both",
                                             "event_date": today - timedelta(days=5), "days_claimed": 7,
                                             "amount_claimed": 1000})
        claim.action_notify()
        claim.action_submit()
        dates = {"date_start": self.project.date_start, "date": self.project.date}
        self.project.write({"date_start": False, "date": False})
        with self.assertRaises(UserError):
            claim.action_agree()
        self.assertEqual(claim.state, "submitted")
        self.project.write(dates)
        claim.action_agree()
        self.assertEqual(claim.state, "agreed")
        self.assertEqual(self.project.date, dates["date"] + timedelta(days=7))
