from odoo import Command
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestVariationOrder(AccountTestInvoicingCommon):

    def test_apply(self):
        self.env.user.group_ids |= self.env.ref("sales_team.group_sale_manager")
        boq = dict(type="service", service_type="manual")
        sanitary = self.env["product.product"].create({"name": "Sanitary set", **boq})
        fm200 = self.env["product.product"].create({"name": "FM-200", **boq})
        so = self.env["sale.order"].create({
            "partner_id": self.partner_a.id,
            "order_line": [Command.create({"product_id": sanitary.id, "product_uom_qty": 100, "price_unit": 24000})],
        })
        so.action_confirm()
        before = so.amount_untaxed
        vo = self.env["hom.variation.order"].create({
            "sale_order_id": so.id, "title": "FM-200 + fewer sanitary sets",
            "line_ids": [
                Command.create({"line_type": "add", "product_id": fm200.id, "quantity": 1, "price_unit": 883000}),
                Command.create({"line_type": "change", "sale_line_id": so.order_line.id, "quantity": -10, "price_unit": 24000}),
            ],
        })
        self.assertEqual(vo.amount_total, 643000)
        vo.action_submit()
        self.assertEqual(vo.name, "VO-01")
        vo.action_approve()
        self.assertEqual(vo.state, "applied")
        self.assertAlmostEqual(so.amount_untaxed, before + 643000)
        self.assertEqual(so.order_line.filtered(lambda l: l.product_id == sanitary).product_uom_qty, 90)
        self.assertTrue(so.order_line.filtered(lambda l: l.display_type == "line_section" and "VO-01" in l.name))
