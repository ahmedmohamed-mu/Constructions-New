from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init
from odoo.addons.zc_project_controls.hooks import _zc_pc_post_init
from odoo.addons.zc_certificate_controls.hooks import _zc_cc_post_init


@tagged("post_install", "-at_install")
class TestMyWork(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("account.group_account_manager")
        company = env.company
        ref = lambda x: env.ref("hom_contracting_base.%s" % x)
        company.write({"hom_retention_owner_product_id": ref("product_retention_owner").id,
                       "hom_retention_sub_product_id": ref("product_retention_sub").id,
                       "hom_sub_advance_product_id": ref("product_sub_advance").id,
                       "hom_sub_advance_recovery_product_id": ref("product_sub_advance_recovery").id})
        _zc_post_init(env)
        _zc_pc_post_init(env)
        _zc_cc_post_init(env)

    def test_my_work_lists_what_waits_for_me(self):
        env = self.env
        owner = env["res.partner"].create({"name": "Owner"})
        item = env["product.product"].create({"name": "Item", "type": "service", "service_type": "manual",
                                              "invoice_policy": "delivery", "service_tracking": "project_only"})
        so = env["sale.order"].create({"partner_id": owner.id, "hom_advance_pct": 0, "hom_retention_pct": 0,
                                       "order_line": [Command.create({"product_id": item.id, "product_uom_qty": 10,
                                                                      "price_unit": 100})]})
        so.action_confirm()
        so.project_ids[:1].user_id = env.user
        env["hom.site.measurement"].create({"sale_line_id": so.order_line.id, "quantity": 5})
        env["zc.claim"].create({"sale_order_id": so.id, "title": "Late access",
                                "event_date": fields.Date.subtract(fields.Date.context_today(env.user), days=20)})
        action = env["zc.my.work"].action_open()
        work = env["zc.my.work"].browse(action["res_id"])
        names = dict((l.sequence, l) for l in work.line_ids)
        self.assertEqual(names[10].count, 1, "measured work without WIR")
        self.assertEqual(names[34].count, 1, "claim notice due within 14 days")
        for line in work.line_ids:
            opened = line.action_open()
            env[opened["res_model"]].search(opened["domain"])  # every domain is valid
        self.assertEqual(work.total, sum(work.line_ids.mapped("count")))
