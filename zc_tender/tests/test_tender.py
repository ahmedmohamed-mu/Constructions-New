from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init


@tagged("post_install", "-at_install")
class TestTender(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("analytic.group_analytic_accounting")
        _zc_post_init(env)
        cls.owner = env["res.partner"].create({"name": "Owner (demo)"})
        cls.item = env["product.product"].create({
            "name": "FN-01 Ceramic floor", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only"})

    def test_bid_decision_then_handover(self):
        env = self.env
        today = fields.Date.context_today(env.user)
        lead = env["crm.lead"].create({"name": "Admin building B5", "type": "opportunity", "partner_id": self.owner.id,
                                       "zc_is_tender": True, "expected_revenue": 1000000, "zc_estimated_cost": 850000,
                                       "zc_submission_deadline": today + timedelta(days=5), "zc_bid_bond_amount": 20000})
        lead.action_zc_load_criteria()
        self.assertEqual(len(lead.zc_criterion_ids), 6)
        self.assertAlmostEqual(lead.zc_expected_margin_pct, 15)
        with self.assertRaises(UserError):
            lead.action_sale_quotations_new()  # no pricing before the decision
        with self.assertRaises(UserError):
            lead.action_zc_bid()  # criteria not scored
        lead.zc_criterion_ids.write({"score": 4})
        self.assertAlmostEqual(lead.zc_score, 80)
        alerts = env["hom.control.tower"]._alerts(env.company, today)
        self.assertTrue(any(isinstance(a.get("action"), dict) and lead.id in a["action"]["domain"][0][2]
                            for a in alerts if a.get("action")))
        lead.action_zc_bid()
        self.assertEqual((lead.zc_decision, lead.zc_decision_by_id), ("bid", env.user))
        bond = env["hom.bank.guarantee"].browse(lead.action_zc_bid_bond()["res_id"])
        self.assertEqual((bond.guarantee_type, bond.amount), ("bid", 20000))

        so = env["sale.order"].create({"partner_id": self.owner.id, "opportunity_id": lead.id,
                                       "hom_advance_pct": 0, "hom_retention_pct": 0,
                                       "zc_tender_assumptions": "Work on two shifts; owner provides water",
                                       "order_line": [Command.create({"product_id": self.item.id,
                                                                      "product_uom_qty": 100, "price_unit": 450})]})
        so.action_confirm()
        with self.assertRaises(UserError):
            so.action_zc_handover_to_execution()  # no budget, no dates
        env["zc.boq.cost.line"].create({"sale_line_id": so.order_line.id, "name": "All-in", "cost_type": "other",
                                        "unit_cost": 380})
        so.project_ids[:1].write({"date_start": today, "date": today + timedelta(days=180)})
        so.invalidate_recordset()
        self.assertTrue(so.zc_ready_budget and so.zc_ready_dates)
        so.action_zc_handover_to_execution()
        self.assertTrue(so.zc_handed_over)
        self.assertAlmostEqual(so.zc_tender_budget, 38000)
        # the execution budget moves, the tender budget stays as the reference
        so.zc_cost_line_ids.unit_cost = 360
        so.invalidate_recordset()
        self.assertAlmostEqual(so.zc_tender_budget, 38000)
        self.assertAlmostEqual(so.zc_budget_vs_tender, -2000)

    def test_no_bid_needs_a_reason(self):
        lead = self.env["crm.lead"].create({"name": "Hospital wing", "type": "opportunity", "zc_is_tender": True,
                                            "partner_id": self.owner.id})
        with self.assertRaises(UserError):
            lead.action_zc_no_bid()
        lead.zc_decision_note = "Funding of the first 4 months above our cash limit"
        lead.action_zc_no_bid()
        self.assertEqual(lead.zc_decision, "no_bid")
        self.assertFalse(lead.active)
