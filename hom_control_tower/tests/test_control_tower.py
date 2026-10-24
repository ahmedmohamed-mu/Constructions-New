from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import new_test_user, tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestControlTower(AccountTestInvoicingCommon):

    def test_dashboard_data(self):
        self.env.user.group_ids |= self.env.ref("sales_team.group_sale_manager") | self.env.ref("project.group_project_manager")
        item = self.env["product.product"].create({"name": "Duct", "type": "service", "service_type": "manual",
                                                   "invoice_policy": "delivery"})
        so = self.env["sale.order"].create({"partner_id": self.partner_a.id, "order_line": [
            Command.create({"product_id": item.id, "product_uom_qty": 100, "price_unit": 1000})]})
        so.action_confirm()
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": so.id})
        cert.action_load_boq()
        cert.line_ids.cumulative_qty = 40
        cert.action_submit()
        cert.action_approve()
        self.env["hom.bank.guarantee"].create({"name": "LG", "partner_id": self.partner_a.id, "amount": 5000,
                                               "expiry_date": "2099-01-01", "sale_order_id": so.id})
        data = self.env["hom.control.tower"].get_dashboard_data()
        row = next(p for p in data["projects"] if p["id"] == so.id)
        self.assertAlmostEqual(row["certified"], 40000)
        self.assertAlmostEqual(row["certified_pct"], 40)
        self.assertGreaterEqual(data["executive"]["kpi"]["contract"], 100000)
        self.assertTrue(data["executive"]["series"])
        self.assertGreaterEqual(data["finance"]["guarantees_issued"], 5000)
        self.assertTrue(any(a["action"]["res_model"] == "hom.progress.certificate" for a in data["alerts"]))
        self.assertTrue(self.env["hom.control.tower"].get_open_action("contracts"))
        engineer = new_test_user(self.env, "ct.eng", groups="project.group_project_user")
        with self.assertRaises(AccessError):
            self.env["hom.control.tower"].with_user(engineer).get_dashboard_data()
