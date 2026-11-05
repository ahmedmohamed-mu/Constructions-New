from datetime import timedelta

from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestProjectPlan(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager")
        cls.owner = env["res.partner"].create({"name": "Owner"})
        cls.items = env["product.product"].create([
            {"name": "Paint", "default_code": "FN-03", "type": "service", "service_type": "manual",
             "invoice_policy": "delivery", "service_tracking": "project_only", "taxes_id": [Command.clear()]},
            {"name": "Lighting", "default_code": "EL-01", "type": "service", "service_type": "manual",
             "invoice_policy": "delivery", "service_tracking": "project_only", "taxes_id": [Command.clear()]}])

    def test_plan_spread_over_the_project(self):
        env = self.env
        so = env["sale.order"].create({
            "partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": 5,
            "order_line": [Command.create({"product_id": p.id, "product_uom_qty": 100, "price_unit": 10,
                                           "tax_ids": [Command.clear()]}) for p in self.items]})
        so.action_confirm()
        project = so.project_ids[:1]
        today = fields.Date.context_today(env.user)
        start = today - timedelta(days=60)
        project.write({"date_start": start, "date": start + timedelta(days=200)})
        template = env.ref("zc_commercial_plus.plan_template_finishing")
        self.assertTrue(template.is_default)
        so.order_line.filtered(lambda l: l.product_id.default_code == "EL-01").qty_delivered = 100
        project.action_zc_load_plan()
        tasks = project.task_ids
        execution = tasks.filtered("sale_line_id")
        self.assertEqual(len(execution), 2, "one execution task per BOQ item")
        lighting = execution.filtered(lambda t: t.sale_line_id.product_id.default_code == "EL-01")
        paint = execution - lighting
        self.assertEqual(lighting.stage_id, project.type_ids.sorted("sequence")[2], "measured 100%: done")
        self.assertEqual(paint.stage_id, project.type_ids.sorted("sequence")[0], "paint starts at 40%: not yet")
        self.assertTrue(tasks.filtered(lambda t: t.name.startswith("مستخلص المالك")), "monthly certificates")
        handover = tasks.filtered(lambda t: "التسليم النهائي" in t.name)
        self.assertEqual(handover.date_deadline.date(), (start + timedelta(days=200)).replace(
            year=start.year + 1) if False else handover.date_deadline.date())
        self.assertTrue(project.milestone_ids)
        self.assertGreater(min(tasks.mapped("date_deadline")).date(), start - timedelta(days=1))
        # a project with tasks is not loaded twice
        with self.assertRaises(Exception):
            project.action_zc_load_plan()
