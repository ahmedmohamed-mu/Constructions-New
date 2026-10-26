from odoo import _, fields, models
from odoo.exceptions import UserError


class HomProjectDossierWizard(models.TransientModel):
    _name = "hom.project.dossier.wizard"
    _description = "Project Dossier"

    project_id = fields.Many2one("project.project", string="Project", required=True,
                                 default=lambda self: self.env.context.get("active_model") == "project.project"
                                 and self.env.context.get("active_id"))
    date_from = fields.Date(string="From", help="Leave empty to include everything since the start of the project.")
    date_to = fields.Date(string="To")

    def _data(self):
        self.ensure_one()
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise UserError(_("The start date must be before the end date."))
        # project_ids travels in data: a report action with data is rendered without docids in the URL
        return {"project_ids": self.project_id.ids,
                "date_from": self.date_from and fields.Date.to_string(self.date_from),
                "date_to": self.date_to and fields.Date.to_string(self.date_to)}

    def action_view(self):
        return self.env.ref("hom_project_dossier.action_report_project_dossier_html").report_action(
            self.project_id, data=self._data())

    def action_print(self):
        return self.env.ref("hom_project_dossier.action_report_project_dossier").report_action(
            self.project_id, data=self._data())
