from datetime import date

from odoo import Command
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from odoo.addons.zc_material_control.hooks import _zc_post_init
from ..hooks import _zc_pc_post_init


@tagged("post_install", "-at_install")
class TestProjectControls(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("analytic.group_analytic_accounting") \
            | env.ref("account.group_account_manager") | env.ref("maintenance.group_equipment_manager")
        cls.tax_s = env["account.tax"].create({"name": "VAT 14 S", "amount": 14, "type_tax_use": "sale"})
        cur = cls.company_data["default_account_receivable"]
        liab = cls.company_data["default_account_revenue"]
        cls.acc_dp = liab.copy({"code": "201030", "name": "Customer advances", "account_type": "liability_current"})
        cls.revenue = liab.copy({"code": "500100", "name": "Contract revenue", "account_type": "income"})
        cls.ga = cls.company_data["default_account_expense"].copy({"code": "410100", "name": "Head office salaries"})
        cls.cost = cls.company_data["default_account_expense"].copy({"code": "510010", "name": "Direct cost"})
        company = env.company
        company.downpayment_account_id = cls.acc_dp
        ref = lambda x: env.ref("hom_contracting_base.%s" % x)
        company.write({"hom_retention_owner_product_id": ref("product_retention_owner").id,
                       "hom_retention_sub_product_id": ref("product_retention_sub").id,
                       "hom_sub_advance_product_id": ref("product_sub_advance").id,
                       "hom_sub_advance_recovery_product_id": ref("product_sub_advance_recovery").id})
        _zc_post_init(env)
        _zc_pc_post_init(env)
        company.zc_vo_owner_agreement = True
        cls.owner = env["res.partner"].create({"name": "Owner (demo)"})
        cls.vendor = env["res.partner"].create({"name": "Vendor"})

        def item(name):
            return env["product.product"].create({
                "name": name, "type": "service", "service_type": "manual", "invoice_policy": "delivery",
                "service_tracking": "project_only", "taxes_id": [Command.set(cls.tax_s.ids)],
                "property_account_income_id": cls.revenue.id})
        cls.item_a, cls.item_b, cls.item_c = item("A Lights"), item("B Paint"), item("C Extra doors")

    # ------------------------------------------------------------------
    def _contract(self, lines, retention=0.0):
        so = self.env["sale.order"].create({
            "partner_id": self.owner.id, "hom_advance_pct": 0, "hom_retention_pct": retention,
            "order_line": [Command.create({"product_id": p.id, "product_uom_qty": q, "price_unit": r})
                           for p, q, r in lines]})
        so.action_confirm()
        project = so.project_ids[:1]
        if not project.account_id:
            plan = self.env["account.analytic.plan"].search([], limit=1)
            project.account_id = self.env["account.analytic.account"].create({"name": so.name, "plan_id": plan.id})
        return so, project

    def _measure(self, so, product, qty, day):
        sol = so.order_line.filtered(lambda l: l.product_id == product)
        return self.env["hom.site.measurement"].create({"sale_line_id": sol.id, "quantity": qty, "date": day})

    def _certify(self, so, period_end):
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": so.id, "period_end": period_end,
                                                            "date": period_end})
        cert.action_load_boq()
        cert.action_submit()
        cert.action_approve()
        if not cert.invoice_id:
            cert.action_create_invoice()
        cert.invoice_id.invoice_date = period_end
        cert.invoice_id.action_post()
        return cert

    def _cost_bill(self, project, amount, day):
        bill = self.env["account.move"].create({
            "move_type": "in_invoice", "partner_id": self.vendor.id, "invoice_date": day,
            "invoice_line_ids": [Command.create({"name": "site cost", "account_id": self.cost.id, "quantity": 1,
                                                 "price_unit": amount, "tax_ids": [Command.clear()],
                                                 "analytic_distribution": {str(project.account_id.id): 100}})]})
        bill.action_post()
        return bill

    @staticmethod
    def _bal(account, moves=None, analytic=None):
        lines = (moves.line_ids if moves else account.env["account.move.line"].search([("account_id", "=", account.id)]))
        lines = lines.filtered(lambda l: l.account_id == account and l.parent_state == "posted")
        if analytic:
            lines = lines.filtered(lambda l: str(analytic.id) in (l.analytic_distribution or {}))
        return round(sum(lines.mapped("balance")), 2)

    # ------------------------------------------------------------------
    def test_earned_value(self):
        so, project = self._contract([(self.item_a, 100, 3000), (self.item_b, 1000, 240)])
        bl = self.env["zc.baseline"].create({"sale_order_id": so.id, "date_start": date(2026, 1, 1),
                                             "date_end": date(2026, 6, 30)})
        bl.action_load_items()
        self.assertEqual(len(bl.item_ids), 2)
        bl.action_generate()
        self.assertAlmostEqual(bl.amount_total, 540000, places=2)
        self.assertEqual(len(bl.line_ids), 12, "2 items x 6 months")
        bl.action_approve()
        self.assertEqual(bl.state, "approved")
        # work: 40 lights + 300 m2 certified at end of February, 50,000 of site cost
        self._measure(so, self.item_a, 40, date(2026, 2, 20))
        self._measure(so, self.item_b, 300, date(2026, 2, 20))
        self._certify(so, date(2026, 2, 28))
        self._cost_bill(project, 50000, date(2026, 2, 10))
        Report = self.env["zc.evm.report"]
        row = Report._zc_compute([project.id], day=date(2026, 2, 28))[0]
        pv = 540000 * 59 / 181  # January + February of a 181-day programme, linear
        self.assertAlmostEqual(row["pv"], pv, delta=0.05)  # monthly values are rounded to the piaster
        self.assertAlmostEqual(row["ev"], 40 * 3000 + 300 * 240, places=2)
        self.assertAlmostEqual(row["ac"], 50000, places=2)
        self.assertAlmostEqual(row["spi"], 192000 / pv, places=3)
        self.assertAlmostEqual(row["cpi"], 192000 / 50000, places=4)
        self.assertEqual(row["status"], "on_track")
        # behind schedule a month later with no new certificate
        late = Report._zc_compute([project.id], day=date(2026, 4, 30))[0]
        self.assertLess(late["spi"], 0.9)
        self.assertEqual(late["status"], "critical")
        curve = Report._zc_curve([project.id], day=date(2026, 3, 15))
        pv_rows = [r for r in curve if r["series"] == "pv"]
        self.assertEqual(len(pv_rows), 6)
        self.assertAlmostEqual(pv_rows[-1]["value"], 540000, places=2)
        self.assertEqual(len([r for r in curve if r["series"] == "ev"]), 3, "EV only up to the status date")
        # a revision supersedes the approved baseline
        action = bl.action_revise()
        bl2 = self.env["zc.baseline"].browse(action["res_id"])
        self.assertEqual(bl2.version, 2)
        bl2.action_approve()
        self.assertEqual(bl.state, "superseded")
        # control tower flags the late project
        alerts = self.env["hom.control.tower"]._alerts(self.env.company, date(2026, 4, 30))
        self.assertTrue(any(a["level"] == "danger" and a["action"] == "zc_project_controls.action_zc_evm_server"
                            for a in alerts))

    def test_overhead_equipment_labour(self):
        so1, p1 = self._contract([(self.item_a, 100, 3000)])
        so2, p2 = self._contract([(self.item_b, 1000, 240)])
        self._measure(so1, self.item_a, 64, date(2026, 2, 15))   # 192,000 revenue
        self._measure(so2, self.item_b, 266.6666667, date(2026, 2, 15))
        self._certify(so1, date(2026, 2, 28))
        self._certify(so2, date(2026, 2, 28))
        company = self.env.company
        # 30,000 head-office salaries in February
        ga_move = self.env["account.move"].create({
            "move_type": "entry", "date": date(2026, 2, 25), "line_ids": [
                Command.create({"name": "salaries", "account_id": self.ga.id, "debit": 30000}),
                Command.create({"name": "salaries", "account_id": self.company_data["default_account_payable"].id,
                                "credit": 30000, "partner_id": self.vendor.id})]})
        ga_move.action_post()
        alloc = self.env["zc.overhead.allocation"].create({
            "date_from": date(2026, 2, 1), "date_to": date(2026, 2, 28), "basis": "revenue",
            "pool_account_ids": [Command.set(self.ga.ids)], "amount": 0})
        self.assertAlmostEqual(alloc.pool_amount, 30000)
        alloc.action_compute()
        self.assertAlmostEqual(alloc.amount, 30000)
        shares = {l.project_id: l.amount for l in alloc.line_ids}
        self.assertAlmostEqual(shares[p1], 22500, places=2)
        self.assertAlmostEqual(shares[p2], 7500, places=2)
        alloc.action_post()
        oh, absorbed = company.zc_overhead_account_id, company.zc_overhead_absorbed_account_id
        self.assertEqual(self._bal(oh, alloc.move_id, p1.account_id), 22500)
        self.assertEqual(self._bal(oh, alloc.move_id, p2.account_id), 7500)
        self.assertEqual(self._bal(absorbed, alloc.move_id), -30000)
        self.assertEqual(self._bal(self.ga) + self._bal(oh) + self._bal(absorbed), 30000, "P&L unchanged")
        # equipment: 1,500 a day, 4 days on p1, 2 days on p2
        crane = self.env["maintenance.equipment"].create({"name": "Scissor lift", "zc_daily_rate": 1500})
        U = self.env["zc.equipment.usage"]
        U.create({"equipment_id": crane.id, "project_id": p1.id, "date": date(2026, 2, 3), "days": 4})
        U.create({"equipment_id": crane.id, "project_id": p2.id, "date": date(2026, 2, 9), "days": 2})
        charge = self.env["zc.equipment.charge"].create({"date_from": date(2026, 2, 1), "date_to": date(2026, 2, 28)})
        charge.action_load()
        self.assertAlmostEqual(charge.amount_total, 9000)
        charge.action_post()
        self.assertTrue(crane.zc_analytic_account_id, "cost centre created on the fly")
        eq_cost, eq_rec = company.zc_equipment_cost_account_id, company.zc_equipment_recovery_account_id
        self.assertEqual(self._bal(eq_cost, charge.move_id, p1.account_id), 6000)
        self.assertEqual(self._bal(eq_cost, charge.move_id, p2.account_id), 3000)
        self.assertEqual(self._bal(eq_rec, charge.move_id, crane.zc_analytic_account_id), -9000)
        cc_lines = self.env["account.analytic.line"].search([(crane.zc_analytic_account_id.plan_id._column_name(), "=",
                                                              crane.zc_analytic_account_id.id)])
        self.assertAlmostEqual(sum(cc_lines.mapped("amount")), 9000, msg="rent recovered on the machine")
        self.assertTrue(all(u.state == "charged" for u in charge.usage_ids))
        again = self.env["zc.equipment.charge"].create({"date_from": date(2026, 2, 1), "date_to": date(2026, 2, 28)})
        again.action_load()
        self.assertFalse(again.usage_ids, "nothing charged twice")
        # weekly daily labour
        sheet = self.env["zc.labour.sheet"].create({
            "project_id": p1.id, "week_start": date(2026, 2, 7),
            "line_ids": [Command.create({"worker": "Saeed", "trade": "helper", "d1": 1, "d2": 1, "d3": 1, "d4": 1,
                                         "d5": 1, "d6": 1, "daily_wage": 400}),
                         Command.create({"worker": "Hamada", "trade": "painter", "d1": 1, "d2": 1, "d3": 1, "d4": 1,
                                         "d5": 1, "d6": 0.5, "daily_wage": 350})]})
        self.assertAlmostEqual(sheet.amount_total, 6 * 400 + 5.5 * 350)
        sheet.action_submit()
        sheet.action_approve()
        self.assertEqual(sheet.state, "billed")
        bill = sheet.bill_id
        self.assertEqual(bill.state, "posted")
        self.assertAlmostEqual(bill.amount_total, 4325, msg="no VAT on daily labour")
        labour_acc = company.zc_labour_product_id.product_tmpl_id.with_company(company).property_account_expense_id
        self.assertEqual(self._bal(labour_acc, bill, p1.account_id), 4325)
        # project p1 actual cost now carries overhead + equipment + labour
        row = self.env["zc.evm.report"]._ac_at(p1, date(2026, 2, 28))
        self.assertAlmostEqual(row, 22500 + 6000 + 4325, places=2)
        # cancelling the equipment charge reverses it and frees the usage
        charge.action_cancel()
        self.assertEqual(self._bal(eq_cost), 0)
        self.assertTrue(all(u.state == "open" for u in charge.usage_ids))

    def test_variation_rate_negotiation(self):
        so, project = self._contract([(self.item_a, 100, 3000)])
        vo = self.env["hom.variation.order"].create({
            "sale_order_id": so.id, "title": "Extra doors", "reason": "consultant", "reference": "SI-07",
            "zc_time_impact_days": 10,
            "line_ids": [Command.create({"line_type": "add", "product_id": self.item_c.id, "quantity": 10,
                                         "price_unit": 5000})]})
        self.assertTrue(vo.zc_requires_agreement)
        vo.action_zc_impact()
        vo.action_zc_submit_owner()
        self.assertEqual(vo.zc_proposed_amount, 50000)
        vo.action_submit()
        vo.action_approve()
        self.assertEqual(vo.state, "approved")
        self.assertTrue(vo.zc_provisional)
        sol_c = so.order_line.filtered(lambda l: l.product_id == self.item_c)
        self.assertTrue(sol_c.zc_provisional, "work may start, item added as provisional")
        alerts = self.env["hom.control.tower"]._alerts(self.env.company, date.today())
        self.assertTrue(any(isinstance(a["action"], dict) and a["action"].get("res_model") == "hom.variation.order"
                            and vo.id in a["action"]["domain"][0][2] for a in alerts))
        # site measures the extra doors and lights; the certificate keeps the doors out
        self._measure(so, self.item_a, 10, date.today())
        self._measure(so, self.item_c, 4, date.today())
        cert = self.env["hom.progress.certificate"].create({"sale_order_id": so.id})
        cert.action_load_boq()
        self.assertNotIn(sol_c, cert.line_ids.sale_line_id)
        self.assertAlmostEqual(cert.amount_current, 30000)
        # negotiation: consultant agrees 4,600 a door
        vo.action_zc_negotiate()
        vo.line_ids.price_unit = 4600
        vo.action_zc_agree()
        self.assertEqual(vo.state, "applied")
        self.assertEqual(vo.zc_agreed_amount, 46000)
        self.assertFalse(sol_c.zc_provisional)
        self.assertEqual(sol_c.price_unit, 4600)
        self.assertEqual(len(so.order_line.filtered(lambda l: l.product_id == self.item_c)), 1, "no duplicate line")
        cert.action_load_boq()
        self.assertIn(sol_c, cert.line_ids.sale_line_id)
        self.assertAlmostEqual(cert.amount_current, 30000 + 4 * 4600)

    def test_variation_without_agreement_applies_directly(self):
        so, project = self._contract([(self.item_a, 100, 3000)])
        vo = self.env["hom.variation.order"].create({
            "sale_order_id": so.id, "title": "Internal", "zc_requires_agreement": False,
            "line_ids": [Command.create({"line_type": "change", "sale_line_id": so.order_line[:1].id,
                                         "quantity": 5, "price_unit": 3000})]})
        vo.action_submit()
        vo.action_approve()
        self.assertEqual(vo.state, "applied")
        self.assertEqual(so.order_line.filtered(lambda l: l.product_id == self.item_a).product_uom_qty, 105)
