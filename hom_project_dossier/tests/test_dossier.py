from odoo.tests import tagged

from odoo.addons.hom_progress_billing.tests.test_progress_certificate import TestProgressCertificate


@tagged("post_install", "-at_install")
class TestProjectDossier(TestProgressCertificate):
    """Re-uses the contract of the certificate tests and checks the dossier against the accounting."""

    def test_dossier_ties_to_accounting(self):
        cert = self._certificate(30, 10)
        cert.action_submit()
        cert.action_approve()
        if not cert.invoice_id:
            cert.action_create_invoice()
        cert.invoice_id.action_post()
        plan = self.env["account.analytic.plan"].search([], limit=1) or self.env["account.analytic.plan"].create({"name": "P"})
        project = self.env["project.project"].create({"name": "Dossier QA", "sale_line_id": self.so.order_line[0].id})
        project.account_id = self.env["account.analytic.account"].create({"name": "Dossier QA", "plan_id": plan.id})
        data = project._hom_dossier()
        self.assertEqual(data["order"], self.so)
        self.assertEqual(len(data["certificates"]), 1)
        self.assertTrue(data["tie_out"])
        for row in data["tie_out"]:
            self.assertTrue(row["ok"], "%s: dossier %s vs ledger %s" % (row["label"], row["system"], row["gl"]))
        self.assertTrue(all(r["rec"] for r in data["invoices"]))
        self.assertIn("/odoo/sale.order/%s" % self.so.id, data["url"](self.so))
        html = self.env["ir.actions.report"]._render_qweb_html(
            "hom_project_dossier.action_report_project_dossier_html", project.ids)[0].decode()
        self.assertIn("Dossier QA", html)
        self.assertIn(cert.name, html)
        self.assertIn("/odoo/hom.progress.certificate/%s" % cert.id, html)
        wizard = self.env["hom.project.dossier.wizard"].create({"project_id": project.id})
        action = wizard.action_print()
        self.assertTrue(action, "the wizard returns the PDF report action")

    def test_wizard_renders_selected_project(self):
        """The wizard sends data (dates), so the report is rendered without docids: it must still find the project."""
        project = self.env["project.project"].create({"name": "Wizard Empty Project"})
        wizard = self.env["hom.project.dossier.wizard"].create({"project_id": project.id})
        for action in (wizard.action_view(), wizard.action_print()):
            action = action.get("context", {}).get("report_action", action) if "data" not in action else action
            self.assertEqual(action["data"]["project_ids"], project.ids)
            html = self.env["ir.actions.report"]._render_qweb_html(
                "hom_project_dossier.action_report_project_dossier_html", None, data=action["data"])[0].decode()
            self.assertIn("Wizard Empty Project", html, "a project with no contract still renders")
