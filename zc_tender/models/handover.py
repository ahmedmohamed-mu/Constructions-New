from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    zc_tender_assumptions = fields.Text(string="Pricing assumptions")
    zc_tender_exclusions = fields.Text(string="Exclusions")
    zc_tender_risks = fields.Text(string="Risks priced / not priced")
    zc_tender_budget = fields.Monetary(string="Tender budget (frozen)", readonly=True, copy=False,
                                       help="Item budget total when the tender was handed over: the reference the "
                                            "execution budget is compared with.")
    zc_budget_vs_tender = fields.Monetary(string="Execution budget vs tender", compute="_compute_zc_budget_vs_tender")
    zc_handed_over = fields.Boolean(string="Handed over to execution", readonly=True, copy=False, tracking=True)
    zc_handover_date = fields.Date(readonly=True, copy=False)
    zc_ready_contract = fields.Boolean(string="Contract signed", compute="_compute_zc_ready")
    zc_ready_budget = fields.Boolean(string="Item budget priced", compute="_compute_zc_ready")
    zc_ready_baseline = fields.Boolean(string="Baseline programme approved", compute="_compute_zc_ready")
    zc_ready_dates = fields.Boolean(string="Project dates set", compute="_compute_zc_ready")
    zc_ready_guarantee = fields.Boolean(string="Performance / advance guarantee", compute="_compute_zc_ready")
    zc_ready_store = fields.Boolean(string="Site store assigned", compute="_compute_zc_ready")

    def _compute_zc_ready(self):
        for order in self:
            project = order.project_ids[:1]
            order.zc_ready_contract = order.state == "sale"
            order.zc_ready_budget = bool(order.zc_cost_line_ids)
            order.zc_ready_baseline = bool(self.env["zc.baseline"].search_count([
                ("sale_order_id", "=", order.id), ("state", "=", "approved")]))
            order.zc_ready_dates = bool(project and project.date_start and project.date)
            order.zc_ready_guarantee = bool(order.hom_guarantee_ids.filtered(
                lambda g: g.guarantee_type in ("performance", "advance") and g.state in ("active", "extended")))
            order.zc_ready_store = bool(project and project.zc_warehouse_id)

    @api.depends("zc_tender_budget", "zc_cost_line_ids.budget_amount")
    def _compute_zc_budget_vs_tender(self):
        for order in self:
            order.zc_budget_vs_tender = (order.zc_budget_total - order.zc_tender_budget) if order.zc_tender_budget \
                else 0.0

    def action_zc_handover_to_execution(self):
        """Tendering hands the project to the project team: what the price assumed, what it excluded and the
        budget it was built on (kept unchanged to measure the improvement or the erosion later)."""
        for order in self:
            missing = []
            if not order.zc_ready_contract:
                missing.append(_("confirmed contract"))
            if not order.zc_ready_budget:
                missing.append(_("item budget (price analysis)"))
            if not order.zc_ready_dates:
                missing.append(_("project start and end dates"))
            if missing:
                raise UserError(_("Before the handover: %s.", ", ".join(missing)))
            order.write({"zc_handed_over": True, "zc_handover_date": fields.Date.context_today(self),
                         "zc_tender_budget": order.zc_budget_total})
            pm = order.project_ids[:1].user_id
            if pm:
                order.activity_schedule("mail.mail_activity_data_todo", user_id=pm.id,
                                        summary=_("Project handed over: read the assumptions, exclusions and risks"))
            order.message_post(body=_("Handed over to execution. Tender budget frozen at %s.", order.zc_tender_budget))
        return True
