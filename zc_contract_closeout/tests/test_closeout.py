from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init
from odoo.addons.zc_project_controls.hooks import _zc_pc_post_init
from odoo.addons.zc_certificate_controls.hooks import _zc_cc_post_init


@tagged("post_install", "-at_install")
class TestCloseout(AccountTestInvoicingCommon):

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
        cls.owner = env["res.partner"].create({"name": "Owner"})
        cls.sub = env["res.partner"].create({"name": "Sub", "supplier_rank": 1})
        cls.item = env["product.product"].create({"name": "Fit-out", "type": "service", "service_type": "manual",
                                                  "invoice_policy": "delivery", "service_tracking": "project_only"})

    def test_closeout_cycle(self):
        today = fields.Date.context_today(self.env.user)
        so = self.env["sale.order"].create({
            "partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 5,
            "zc_dlp_months": 12, "zc_retention_prelim_pct": 50,
            "order_line": [Command.create({"product_id": self.item.id, "product_uom_qty": 100, "price_unit": 1000})]})
        so.action_confirm()
        project = so.project_ids[:1]
        if not project.account_id:
            plan = self.env["account.analytic.plan"].search([], limit=1)
            project.account_id = self.env["account.analytic.account"].create({"name": "P", "plan_id": plan.id})
        perf = self.env["hom.bank.guarantee"].create({
            "name": "LG-PERF-1", "direction": "issued", "guarantee_type": "performance", "partner_id": self.owner.id,
            "sale_order_id": so.id, "amount": 5000, "expiry_date": today + relativedelta(months=6)})
        # owner certificate: whole contract executed
        self.env["hom.site.measurement"].create({"sale_line_id": so.order_line.id, "quantity": 100})
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": so.id})
        cert.action_load_boq()
        cert.action_submit()
        cert.action_approve()
        cert.action_create_invoice()
        cert.invoice_id.action_post()
        self.assertAlmostEqual(so.hom_retention_balance, 5000)
        # subcontract on the same project
        svc = self.env["product.product"].create({"name": "Labour", "type": "service", "purchase_method": "receive"})
        po = self.env["purchase.order"].create({
            "partner_id": self.sub.id, "hom_is_subcontract": True, "hom_retention_pct": 5,
            "order_line": [Command.create({"product_id": svc.id, "product_qty": 100, "price_unit": 300,
                                           "analytic_distribution": {str(project.account_id.id): 100}})]})
        po.button_confirm()
        sub_lg = self.env["hom.bank.guarantee"].create({
            "name": "LG-SUB-1", "direction": "received", "guarantee_type": "performance", "partner_id": self.sub.id,
            "purchase_order_id": po.id, "amount": 1500, "expiry_date": today + relativedelta(months=18)})
        sc = self.env["hom.subcontract.certificate"].create({"purchase_order_id": po.id})
        sc.action_load_items()
        sc.line_ids.cumulative_qty = 100
        sc.action_submit()
        sc.action_approve()
        sc.action_create_bill()
        sc.bill_id.invoice_date = today
        sc.bill_id.action_post()
        self.assertAlmostEqual(po.hom_retention_balance, 1500)
        self.assertEqual(so._zc_subcontracts(), po)

        # preliminary handover with one snag
        pre = self.env["zc.handover"].create({"sale_order_id": so.id, "kind": "preliminary", "date": today,
                                              "snag_ids": [Command.create({"name": "Door alignment"})]})
        pre.action_sign()
        self.assertEqual(so.zc_prelim_handover_id, pre)
        self.assertEqual(so.zc_dlp_end, today + relativedelta(months=12))
        self.assertEqual(so.zc_closeout_state, "dlp")
        half = pre.release_move_ids
        self.assertAlmostEqual(half.amount_total, 2500, msg="50% of the retention at the preliminary handover")
        self.assertTrue(perf.activity_ids, "performance guarantee shorter than the defects liability period")
        half.action_post()
        so.invalidate_recordset()
        self.assertAlmostEqual(so.hom_retention_balance, 2500)

        # the defects liability period is over
        pre.date = today - relativedelta(months=13)
        so.invalidate_recordset()
        self.assertEqual(so.zc_closeout_state, "dlp_over")
        alerts = self.env["hom.control.tower"]._alerts(self.env.company, today)
        self.assertTrue(any(a["action"].get("res_model") == "sale.order" and so.id in a["action"]["domain"][0][2]
                            for a in alerts if isinstance(a.get("action"), dict)))

        final = self.env["zc.handover"].create({"sale_order_id": so.id, "kind": "final", "date": today})
        with self.assertRaises(UserError):
            final.action_sign()
        pre.snag_ids.action_done()
        final.action_sign()
        self.assertEqual(so.zc_closeout_state, "closed")
        owner_rel = final.release_move_ids.filtered(lambda m: m.move_type == "out_invoice")
        sub_rel = final.release_move_ids.filtered(lambda m: m.move_type == "in_invoice")
        self.assertAlmostEqual(owner_rel.amount_total, 2500)
        self.assertAlmostEqual(sub_rel.amount_total, 1500)
        self.assertEqual(final.retention_amount, 2500)
        self.assertEqual((perf | sub_lg).mapped("state"), ["released", "released"])
        html = self.env["ir.actions.report"]._render_qweb_html(
            "zc_contract_closeout.action_report_final_account", so.ids)[0].decode()
        self.assertIn("الحساب الختامي", html)
        fa = so._zc_final_account()
        self.assertAlmostEqual(fa["final_work"], 100000)
        self.assertAlmostEqual(fa["retention_held"], 5000)
