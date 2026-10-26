from odoo import _, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    hom_certificate_frequency = fields.Selection(
        [("monthly", "Monthly / شهري"), ("milestone", "On milestones / بالمراحل"), ("on_demand", "On demand / عند الطلب")],
        string="Certificate Frequency", default="monthly", required=True, tracking=True,
        help="Monthly: a draft certificate is prepared on the certificate day when there is new site work.\n"
             "On milestones: a draft is prepared when a project milestone is marked as reached.\n"
             "On demand: no automatic drafts; the site engineer creates the certificate when needed.")
    hom_certificate_ids = fields.One2many("hom.progress.certificate", "sale_order_id", string="Progress Certificates")
    hom_certificate_count = fields.Integer(compute="_compute_hom_certificate_count")
    hom_certified_amount = fields.Monetary(string="Certified to Date", compute="_compute_hom_contract_control")
    hom_certified_pct = fields.Float(string="Certified %", compute="_compute_hom_contract_control")
    hom_advance_received = fields.Monetary(string="Advance Invoiced", compute="_compute_hom_contract_control")
    hom_advance_outstanding = fields.Monetary(string="Advance to Recover", compute="_compute_hom_contract_control")
    hom_open_measurement_count = fields.Integer(string="Uncertified Measurements",
                                                compute="_compute_hom_contract_control")

    def _compute_hom_certificate_count(self):
        for order in self:
            order.hom_certificate_count = len(order.hom_certificate_ids)

    def _compute_hom_contract_control(self):
        Measurement = self.env["hom.site.measurement"].sudo()
        for order in self:
            last = order.hom_certificate_ids.filtered(lambda c: c.state in ("approved", "invoiced")).sorted("number")[-1:]
            order.hom_certified_amount = last.amount_cumulative if last else 0.0
            order.hom_certified_pct = last.progress_pct if last else 0.0
            received = outstanding = 0.0
            for dp in order.order_line.filtered(lambda l: l.is_downpayment and not l.display_type):
                invoiced = recovered = 0.0
                for aml in dp.invoice_lines.filtered(lambda a: a.move_id.state == "posted"):
                    qty = aml.quantity * (-1 if aml.move_id.move_type == "out_refund" else 1)
                    if qty > 0:
                        invoiced += qty
                    else:
                        recovered -= qty
                received += invoiced * dp.price_unit
                outstanding += max(invoiced - recovered, 0.0) * dp.price_unit
            order.hom_advance_received = received
            order.hom_advance_outstanding = outstanding
            order.hom_open_measurement_count = Measurement.search_count(
                [("sale_order_id", "=", order.id), ("state", "=", "open")]) if order.id else 0

    def action_hom_certificates(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_progress_billing.action_hom_progress_certificate")
        action["domain"] = [("sale_order_id", "=", self.id)]
        action["context"] = {"default_sale_order_id": self.id}
        return action

    def action_hom_measurements(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_progress_billing.action_hom_site_measurement")
        action["domain"] = [("sale_order_id", "=", self.id)]
        action["context"] = {"search_default_open": 1}
        action["name"] = _("Site Measurements")
        return action
