from odoo import _, api, fields, models
from odoo.exceptions import UserError


class ZcDailyReport(models.Model):
    _inherit = "zc.daily.report"

    toolbox_talk = fields.Boolean(string="Toolbox talk held")
    permits = fields.Char(string="Work permits issued", help="Hot work, work at height, excavation...")
    safety_observations = fields.Text(string="Safety observations")
    incident = fields.Selection([("none", "No incident"), ("near_miss", "Near miss"), ("first_aid", "First aid"),
                                 ("medical", "Medical treatment"), ("lost_time", "Lost-time injury")],
                                default="none", required=True, tracking=True)
    incident_note = fields.Text(string="Incident description & actions")

    @api.constrains("incident", "incident_note")
    def _check_incident(self):
        for rec in self:
            if rec.incident not in ("none", False) and not rec.incident_note:
                raise UserError(_("Describe the incident and the actions taken."))
