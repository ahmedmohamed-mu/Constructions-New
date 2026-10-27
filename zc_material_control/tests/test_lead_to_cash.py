from datetime import timedelta

from odoo import Command, fields
from odoo.tests import tagged
from odoo.addons.account.tests.common import AccountTestInvoicingCommon

from ..hooks import _zc_post_init


@tagged("post_install", "-at_install")
class TestLeadToCash(AccountTestInvoicingCommon):
    """One tender from the lead to the cash, every number checked against the general ledger."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.group_ids |= env.ref("sales_team.group_sale_manager") | env.ref("project.group_project_manager") \
            | env.ref("purchase.group_purchase_manager") | env.ref("stock.group_stock_manager") \
            | env.ref("analytic.group_analytic_accounting")
        cls.today = fields.Date.context_today(env.user)
        cls.tax_s = env["account.tax"].create({"name": "VAT 14 S", "amount": 14, "type_tax_use": "sale"})
        cls.tax_p = env["account.tax"].create({"name": "VAT 14 P", "amount": 14, "type_tax_use": "purchase"})
        cur = cls.company_data["default_account_receivable"]
        liab = cls.company_data["default_account_revenue"]
        cls.acc_ret_c = cur.copy({"code": "102020", "name": "Retention receivable", "account_type": "asset_current",
                                  "reconcile": False})
        cls.acc_sub_adv = cur.copy({"code": "102021", "name": "Subcontractor advances", "account_type": "asset_current",
                                    "reconcile": False})
        cls.acc_dp = liab.copy({"code": "201030", "name": "Customer advances", "account_type": "liability_current"})
        cls.acc_ret_s = liab.copy({"code": "201031", "name": "Retention payable", "account_type": "liability_current"})
        cls.revenue = liab.copy({"code": "500100", "name": "Contract revenue", "account_type": "income"})
        cls.vat_out = liab.copy({"code": "220300", "name": "VAT payable", "account_type": "liability_current"})
        cls.vat_in = cur.copy({"code": "104040", "name": "VAT receivable", "account_type": "asset_current",
                               "reconcile": False})
        for tax, account in ((cls.tax_s, cls.vat_out), (cls.tax_p, cls.vat_in)):
            (tax.invoice_repartition_line_ids | tax.refund_repartition_line_ids).filtered(
                lambda r: r.repartition_type == "tax").account_id = account
        cls.cost = cls.company_data["default_account_expense"].copy({"code": "510010", "name": "Direct cost"})
        company = env.company
        company.downpayment_account_id = cls.acc_dp
        ref = lambda x: env.ref("hom_contracting_base.%s" % x)
        company.write({"hom_retention_owner_product_id": ref("product_retention_owner").id,
                       "hom_retention_sub_product_id": ref("product_retention_sub").id,
                       "hom_sub_advance_product_id": ref("product_sub_advance").id,
                       "hom_sub_advance_recovery_product_id": ref("product_sub_advance_recovery").id})
        for product in (company.hom_retention_owner_product_id, company.hom_retention_sub_product_id,
                        company.hom_sub_advance_product_id, company.hom_sub_advance_recovery_product_id):
            product.product_tmpl_id.write({"property_account_income_id": False, "property_account_expense_id": False})
        _zc_post_init(env)
        cls.site = env["stock.warehouse"].create({"name": "Site L2C", "code": "L2C"})
        cls.vendor = env["res.partner"].create({"name": "LED vendor", "zc_return_days": 14})
        cls.sub_partner = env["res.partner"].create({"name": "Installer"})
        cls.owner = env["res.partner"].create({"name": "Owner (demo)"})
        cls.led = env["product.product"].create({
            "name": "LED panel", "type": "consu", "is_storable": True, "standard_price": 2000,
            "supplier_taxes_id": [Command.set(cls.tax_p.ids)], "property_account_expense_id": cls.cost.id})
        cls.labour = env["product.product"].create({
            "name": "Install lights", "type": "service", "supplier_taxes_id": [Command.set(cls.tax_p.ids)],
            "property_account_expense_id": cls.cost.id})
        cls.item = env["product.product"].create({
            "name": "EL-01 Light fitting", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only", "taxes_id": [Command.set(cls.tax_s.ids)],
            "property_account_income_id": cls.revenue.id})
        cls.paint_item = env["product.product"].create({
            "name": "FN-03 Paint works", "type": "service", "service_type": "manual", "invoice_policy": "delivery",
            "service_tracking": "project_only", "taxes_id": [Command.set(cls.tax_s.ids)],
            "property_account_income_id": cls.revenue.id})

    def _balance(self, account, moves):
        lines = moves.line_ids.filtered(lambda l: l.account_id == account and l.parent_state == "posted")
        return round(sum(lines.mapped("balance")), 2)

    def _pay(self, move):
        self.env["account.payment.register"].with_context(active_model="account.move", active_ids=move.ids) \
            .create({})._create_payments()

    def test_lead_to_cash(self):
        env = self.env
        # 1. tender won
        lead = env["crm.lead"].create({"name": "Tender - Admin building", "type": "opportunity",
                                       "partner_id": self.owner.id, "expected_revenue": 540000}) \
            if "crm.lead" in env else None
        so = env["sale.order"].create({
            "partner_id": self.owner.id, "hom_advance_pct": 15, "hom_retention_pct": 5,
            "zc_stage_valuation": True, "zc_supplied_pct": 60, "zc_installed_pct": 75,
            **({"opportunity_id": lead.id} if lead else {}),
            "order_line": [Command.create({"product_id": self.item.id, "product_uom_qty": 100, "price_unit": 3000}),
                           Command.create({"product_id": self.paint_item.id, "product_uom_qty": 1000,
                                           "price_unit": 240, "zc_installed_pct": 100})]})
        so.action_confirm()
        if lead:
            lead.action_set_won()
            self.assertTrue(lead.won_status == "won" if "won_status" in lead._fields else lead.stage_id.is_won)
        project = so.project_ids[:1]
        self.assertTrue(project, "the contract opens the project")
        if not project.account_id:
            plan = env["account.analytic.plan"].search([], limit=1)
            project.account_id = env["account.analytic.account"].create({"name": "L2C", "plan_id": plan.id})
        project.zc_warehouse_id = self.site
        sol = so.order_line.filtered(lambda l: l.product_id == self.item)
        sol_paint = so.order_line.filtered(lambda l: l.product_id == self.paint_item)
        env["zc.boq.cost.line"].create({"sale_line_id": sol.id, "product_id": self.led.id, "unit_cost": 2000})
        # 2. advance 15% against the guarantee
        wiz = env["sale.advance.payment.inv"].with_context(active_model="sale.order", active_ids=so.ids,
                                                             active_id=so.id).create(
            {"advance_payment_method": "percentage", "amount": 15})
        wiz.create_invoices()
        adv = so.invoice_ids
        adv.action_post()
        self.assertAlmostEqual(adv.amount_untaxed, 81000)
        self._pay(adv)
        # 3. engineering: approved shop drawing 97
        sub = env["hom.submittal"].create({"title": "Lights SD", "submittal_type": "shop_drawing",
                                          "project_id": project.id, "zc_sale_line_id": sol.id, "zc_sd_qty": 97})
        sub.action_submit()
        sub.action_code_a()
        self.assertEqual(sol.zc_sd_qty, 97)
        # 4. purchasing within the shop drawing, received on site, vendor bill
        dist = {str(project.account_id.id): 100}
        po = env["purchase.order"].create({"partner_id": self.vendor.id, "picking_type_id": self.site.in_type_id.id,
                                           "order_line": [Command.create({"product_id": self.led.id, "product_qty": 97,
                                                                          "price_unit": 2000,
                                                                          "analytic_distribution": dist})]})
        self.assertEqual(po.order_line.zc_boq_line_id, sol, "linked to the BOQ item from the project analytic")
        self.assertEqual(po.order_line.zc_check, "ok")
        po.button_confirm()
        for picking in po.picking_ids:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
            picking.move_ids.picked = True
            picking.button_validate()
        self.assertTrue(po.picking_ids.zc_return_deadline)
        po.action_create_invoice()
        bill = po.invoice_ids
        bill.invoice_date = self.today
        bill.action_post()
        # 5. site: 97 supplied, 60 installed, 400 m2 painted
        M = env["hom.site.measurement"]
        M.create({"sale_line_id": sol.id, "quantity": 97, "zc_stage": "supplied", "date": self.today - timedelta(days=6)})
        M.create({"sale_line_id": sol.id, "quantity": 60, "zc_stage": "installed", "date": self.today - timedelta(days=5)})
        M.create({"sale_line_id": sol_paint.id, "quantity": 400, "zc_stage": "installed",
                  "date": self.today - timedelta(days=5)})
        # 6. subcontract (labour only) with advance, back-to-back on installed quantities
        sc = env["purchase.order"].create({
            "partner_id": self.sub_partner.id, "hom_is_subcontract": True, "hom_advance_amount": 1000,
            "hom_advance_recovery_pct": 10, "hom_retention_pct": 5,
            "order_line": [Command.create({"product_id": self.labour.id, "product_qty": 100, "price_unit": 100,
                                           "hom_boq_line_id": sol.id, "analytic_distribution": dist})]})
        sc.button_confirm()
        sc.action_hom_advance_bill()
        sc_adv = sc.hom_advance_bill_ids
        sc_adv.invoice_date = self.today
        sc_adv.action_post()
        # 7. owner certificate with stage valuation
        cert = env["hom.progress.certificate"].create({"sale_order_id": so.id,
                                                       "period_end": self.today - timedelta(days=1)})
        cert.action_load_boq()
        # lights: 60 x 75% + 37 x 60% = 67.2 units; paint 400 x 100%
        self.assertAlmostEqual(cert.amount_current, 67.2 * 3000 + 400 * 240)
        self.assertAlmostEqual(cert.amount_advance_recovery, -44640)
        self.assertAlmostEqual(cert.amount_retention, -14880)
        cert.action_submit()
        cert.action_approve()
        if not cert.invoice_id:
            cert.action_create_invoice()
        inv = cert.invoice_id
        inv.action_post()
        self.assertAlmostEqual(inv.amount_untaxed, 238080)
        self.assertAlmostEqual(inv.amount_tax, 35414.4)
        self.assertAlmostEqual(inv.amount_total, 273494.4)
        self._pay(inv)
        self.assertEqual(inv.payment_state in ("paid", "in_payment"), True)
        # 8. subcontractor certificate: 60 installed
        scert = env["hom.subcontract.certificate"].create({"purchase_order_id": sc.id, "date": self.today})
        scert.action_load_items()
        self.assertAlmostEqual(scert.line_ids.cumulative_qty, 60)
        scert.action_submit()
        scert.action_approve()
        scert.action_create_bill()
        sbill = scert.bill_id
        sbill.invoice_date = self.today
        sbill.action_post()
        # 9. general ledger
        customer = adv | inv
        vendor = bill | sc_adv | sbill
        self.assertEqual(self._balance(self.acc_dp, customer), -(81000 - 44640))
        self.assertEqual(self._balance(self.acc_ret_c, customer), 14880)
        self.assertEqual(self._balance(self.revenue, customer), -(67.2 * 3000 + 400 * 240))
        self.assertAlmostEqual(self._balance(self.vat_out, customer), -46754.4)
        self.assertAlmostEqual(self._balance(self.vat_in, bill), 97 * 2000 * 0.14)
        self.assertAlmostEqual(sum(customer.mapped("amount_residual")), 0.0)
        self.assertEqual(self._balance(self.acc_sub_adv, vendor), 1000 - 600)
        self.assertEqual(self._balance(self.acc_ret_s, vendor), -300)
        self.assertEqual(self._balance(self.cost, vendor), 97 * 2000 + 6000)
        # analytic: project cost and revenue
        aal = env["account.analytic.line"].search([("account_id", "=", project.account_id.id)])
        self.assertAlmostEqual(sum(aal.filtered(lambda l: l.amount < 0).mapped("amount")), -(97 * 2000 + 6000))
        # 10. dossier ties to the ledger, material truth, control tower
        if hasattr(project, "_hom_dossier"):  # project dossier installed (it is on the demo databases)
            data = project._hom_dossier()
            for row in data["tie_out"]:
                self.assertTrue(row["ok"], "%s: dossier %s vs ledger %s" % (row["label"], row["system"], row["gl"]))
        env["zc.material.recon"].action_refresh()
        row = env["zc.material.recon"].search([("project_id", "=", project.id), ("product_id", "=", self.led.id)])
        self.assertEqual((row.required_qty, row.received_qty, row.installed_qty, row.surplus_qty), (97, 97, 60, 0))
        self.assertEqual(row.recommendation, "ok")
        tower = env["hom.control.tower"].get_dashboard_data()
        self.assertTrue(any(p["id"] == so.id for p in tower["projects"]))
