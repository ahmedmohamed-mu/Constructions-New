from odoo import fields, models


class ProjectProject(models.Model):
    _inherit = "project.project"

    hom_certificate_count = fields.Integer(compute="_compute_hom_certificate_count")

    def _compute_hom_certificate_count(self):
        data = self.env["hom.progress.certificate"]._read_group(
            [("project_id", "in", self.ids)], ["project_id"], ["__count"])
        counts = {project.id: count for project, count in data}
        for project in self:
            project.hom_certificate_count = counts.get(project.id, 0)

    def action_hom_certificates(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_progress_billing.action_hom_progress_certificate")
        action["domain"] = [("project_id", "=", self.id)]
        action["context"] = {"default_sale_order_id": self.reinvoiced_sale_order_id.id or self.sale_order_id.id}
        return action
