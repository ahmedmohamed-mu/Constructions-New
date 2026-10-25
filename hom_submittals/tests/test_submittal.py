from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSubmittal(TransactionCase):

    def test_cycle_codes_revision_overdue(self):
        project = self.env["project.project"].create({"name": "P", "user_id": self.env.user.id})
        consultant = self.env["res.partner"].create({"name": "Consultant"})
        sub = self.env["hom.submittal"].create({"title": "Insulation sample", "project_id": project.id,
                                                "consultant_id": consultant.id, "submittal_type": "sample"})
        self.assertTrue(sub.name.startswith("SUB-"))
        sub.action_submit()
        self.assertEqual(sub.state, "submitted")
        self.assertEqual(sub.due_date, fields.Date.today() + timedelta(days=14))
        sub.submitted_date = fields.Date.today() - timedelta(days=20)
        self.assertTrue(sub.is_overdue)
        self.assertEqual(sub.days_overdue, 6)
        self.assertIn(sub, self.env["hom.submittal"].search([("is_overdue", "=", True)]))
        self.env["hom.submittal"]._cron_overdue()
        self.assertTrue(sub.activity_ids)
        sub.action_code_c()
        self.assertEqual(sub.state, "revise")
        action = sub.action_resubmit()
        rev1 = self.env["hom.submittal"].browse(action["res_id"])
        self.assertEqual((rev1.revision, rev1.name, sub.state), (1, sub.name, "superseded"))
        rev1.action_submit()
        rev1.action_code_a()
        self.assertEqual(rev1.state, "approved")
        with self.assertRaises(UserError):
            rev1.action_code_b()
        self.assertEqual(project.hom_submittal_open_count, 0)
