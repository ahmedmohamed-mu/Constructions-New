from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

ROLES = [("pm", "Project manager"), ("qs", "Technical office"), ("site", "Site"),
         ("procurement", "Procurement"), ("accounts", "Accounts")]
ROLE_TAGS = {"pm": "إدارة المشروع", "qs": "المكتب الفني", "site": "الموقع", "procurement": "المشتريات",
             "accounts": "الحسابات"}

# When each trade usually runs inside the execution window of a finishing job (% of the contract duration).
TRADE_WINDOWS = [
    ("EL", 10, 60), ("PL", 10, 55), ("MF", 15, 60), ("FN-04", 25, 70), ("FN-03", 40, 90), ("FN-01", 30, 80),
    ("FN-02", 35, 85), ("FN-05", 45, 85), ("FN-06", 60, 92), ("FN-07", 55, 90), ("AR", 60, 92),
]


class ZcProjectPlanTemplate(models.Model):
    _name = "zc.project.plan.template"
    _description = "Project Plan Template"
    _order = "sequence, id"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    is_default = fields.Boolean(string="Default", help="Loaded by itself on every new contract at the handover to "
                                                         "execution.")
    line_ids = fields.One2many("zc.project.plan.template.line", "template_id", string="Plan", copy=True)
    note = fields.Text()


class ZcProjectPlanTemplateLine(models.Model):
    _name = "zc.project.plan.template.line"
    _description = "Project Plan Template Line"
    _order = "sequence, id"

    template_id = fields.Many2one("zc.project.plan.template", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True)
    phase = fields.Selection([("mobilization", "Mobilization"), ("engineering", "Engineering"),
                              ("procurement", "Procurement"), ("execution", "Execution"),
                              ("billing", "Certificates"), ("closeout", "Close-out")], required=True,
                             default="execution")
    kind = fields.Selection([("task", "Task"), ("milestone", "Milestone"),
                             ("boq", "One task per BOQ item"), ("monthly", "Every month")],
                            required=True, default="task",
                            help="One task per BOQ item: the execution of each contract item, timed by its trade and "
                                 "following the quantities measured on site.\nEvery month: one task a month for the "
                                 "whole duration (e.g. the owner certificate).")
    start_pct = fields.Float(string="Start %", help="Start, in % of the project duration.")
    end_pct = fields.Float(string="End %", default=100.0, help="Deadline, in % of the project duration. Above 100 "
                                                                 "is after the end date (e.g. the final handover).")
    extra_months = fields.Integer(string="Months after the end",
                                  help="Added after the end date, e.g. 12 for the final handover after the defects "
                                       "liability period.")
    role = fields.Selection(ROLES, default="pm")
    chain = fields.Boolean(string="After the previous task", default=True,
                           help="The task waits for the previous task of the same phase (Gantt dependency).")
    description = fields.Text()


class ProjectProject(models.Model):
    _inherit = "project.project"

    zc_plan_template_id = fields.Many2one("zc.project.plan.template", string="Plan template", copy=False)

    # ------------------------------------------------------------------
    def action_zc_load_plan(self):
        for project in self:
            template = project.zc_plan_template_id or self.env["zc.project.plan.template"].search(
                [("is_default", "=", True)], limit=1) or self.env["zc.project.plan.template"].search([], limit=1)
            if not template:
                raise UserError(_("Create a project plan template first (Project > Configuration)."))
            if not project.date_start or not project.date:
                raise UserError(_("Set the start and end dates of %s first.", project.display_name))
            if project.task_ids:
                raise UserError(_("%s already has tasks: the plan is loaded on an empty project only.",
                                  project.display_name))
            project._zc_build_plan(template)
        return True

    def _zc_point(self, pct, extra_months=0):
        """The date at pct % of the project duration."""
        self.ensure_one()
        days = (self.date - self.date_start).days
        day = self.date_start + timedelta(days=round(days * pct / 100.0))
        return day + relativedelta(months=extra_months) if extra_months else day

    def _zc_stages(self):
        """New / in progress / done stages of the project (created if the project has none)."""
        stages = self.type_ids.sorted("sequence")
        if len(stages) < 3:
            stages = self.env["project.task.type"].search([("project_ids", "=", False), ("user_id", "=", False)],
                                                          order="sequence")
            if len(stages) < 3:
                stages = self.env["project.task.type"].create([
                    {"name": _("New"), "sequence": 1}, {"name": _("In Progress"), "sequence": 2},
                    {"name": _("Done"), "sequence": 3, "fold": True}])
            self.type_ids = [Command.link(s.id) for s in stages]
        return stages[0], stages[1], stages[2]

    def _zc_role_tag(self, role):
        name = ROLE_TAGS.get(role)
        if not name:
            return self.env["project.tags"]
        tag = self.env["project.tags"].search([("name", "=", name)], limit=1)
        return tag or self.env["project.tags"].create({"name": name})

    def _zc_build_plan(self, template):
        self.ensure_one()
        Task = self.env["project.task"].with_context(mail_create_nolog=True, mail_notrack=True,
                                                     tracking_disable=True)
        today = fields.Date.context_today(self)
        order = self.sale_order_id
        closed = order and "zc_closeout_state" in order._fields and order.zc_closeout_state or "running"
        new, doing, done = self._zc_stages()
        vals = {"zc_plan_template_id": template.id}
        if "allow_task_dependencies" in self._fields:
            vals["allow_task_dependencies"] = True
        if "allow_milestones" in self._fields:
            vals["allow_milestones"] = True
        self.write(vals)
        has_planning = "planned_date_begin" in Task._fields
        previous = {}
        milestones = self.env["project.milestone"]
        seq = 0

        def status(begin, end, progress=None):
            if closed == "closed":
                return "done"
            if progress is not None:
                return "done" if progress >= 0.999 else ("doing" if progress > 0 else
                                                         ("doing" if begin <= today else "new"))
            if end < today:
                return "done"
            return "doing" if begin <= today else "new"

        def make(line, name, begin, end, state, description=None, sale_line=False):
            nonlocal seq
            seq += 1
            stage = {"new": new, "doing": doing, "done": done}[state]
            task_vals = {
                "name": name, "project_id": self.id, "sequence": seq, "stage_id": stage.id,
                "date_deadline": fields.Datetime.to_datetime(end).replace(hour=15) if end else False,
                "description": description or line.description or False,
                "tag_ids": [Command.set(self._zc_role_tag(line.role).ids)],
                # the project manager owns the plan (never the user running the upgrade, e.g. OdooBot)
                "user_ids": [Command.set(self.user_id.ids)],
            }
            if has_planning and begin:
                task_vals["planned_date_begin"] = fields.Datetime.to_datetime(begin).replace(hour=8)
            if "sale_line_id" in Task._fields:
                # only the execution tasks belong to a BOQ item (the others would inherit the project's line)
                task_vals["sale_line_id"] = sale_line.id if sale_line else False
            task = Task.create(task_vals)
            if state == "done" and "state" in task._fields:
                task.state = "1_done"
            if line.chain and previous.get(line.phase) and "depend_on_ids" in task._fields:
                task.depend_on_ids = [Command.link(previous[line.phase].id)]
            previous[line.phase] = task
            return task

        for line in template.line_ids:
            begin = self._zc_point(line.start_pct)
            end = self._zc_point(line.end_pct, line.extra_months)
            if line.kind == "milestone":
                reached = closed == "closed" or (end < today and line.end_pct < 100) or (
                    closed in ("dlp", "dlp_over") and not line.extra_months)
                milestones |= self.env["project.milestone"].create({
                    "name": line.name, "project_id": self.id, "deadline": end, "is_reached": reached})
            elif line.kind == "task":
                state = status(begin, end)
                if line.extra_months and closed != "closed" and state == "done":
                    state = "doing"  # after the defects liability period: done with the final handover only
                make(line, line.name, begin, end, state)
            elif line.kind == "monthly":
                day = (self.date_start.replace(day=1) + relativedelta(months=1)) - timedelta(days=1)
                n = 0
                while day <= self.date + relativedelta(months=1):
                    n += 1
                    certified = order and order.hom_certificate_ids.filtered(
                        lambda c, d=day: c.state in ("approved", "invoiced") and c.date
                        and c.date.year == d.year and c.date.month == d.month) if "hom_certificate_ids" in order._fields else False
                    handed_over = closed in ("closed", "dlp", "dlp_over") and day < today
                    state = "done" if (certified or handed_over or closed == "closed") else (
                        "doing" if day - timedelta(days=10) <= today else "new")
                    make(line, "%s %s - %s" % (line.name, n, day.strftime("%m/%Y")), day - timedelta(days=10), day,
                         state)
                    day = (day + timedelta(days=1) + relativedelta(months=1)) - timedelta(days=1)
            elif line.kind == "boq" and order:
                for sol in order.order_line.filtered(lambda l: not l.display_type and not l.is_downpayment
                                                     and l.product_uom_qty > 0):
                    code = sol.product_id.default_code or ""
                    window = next(((s, e) for prefix, s, e in TRADE_WINDOWS if code.startswith(prefix)),
                                  (line.start_pct, line.end_pct))
                    b, e = self._zc_point(window[0]), self._zc_point(window[1])
                    progress = sol.qty_delivered / sol.product_uom_qty if sol.product_uom_qty else 0.0
                    desc = _("Executed %(d)s of %(q)s %(u)s (%(p)s%%).", d=round(sol.qty_delivered, 2),
                             q=round(sol.product_uom_qty, 2), u=sol.product_uom_id.name, p=round(progress * 100))
                    name = "%s - %s" % (line.name, sol.product_id.display_name)
                    previous.pop("execution", None)  # the trades run side by side, not one after the other
                    make(line, name, b, e, status(b, e, progress), desc, sol)
        if order:
            order.message_post(body=_("Project plan loaded from %(t)s: %(n)s tasks, %(m)s milestones.",
                                      t=template.name, n=len(self.task_ids), m=len(milestones)))
        return True

    @api.model
    def _zc_load_plans_everywhere(self):
        """Give a plan to every contract project that has dates and no task yet."""
        projects = self.search([("sale_order_id", "!=", False), ("date_start", "!=", False), ("date", "!=", False)])
        template = self.env["zc.project.plan.template"].search([("is_default", "=", True)], limit=1)
        if not template:
            return
        for project in projects.filtered(lambda p: not p.task_ids):
            project._zc_build_plan(template)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_zc_handover_to_execution(self):
        res = super().action_zc_handover_to_execution()
        template = self.env["zc.project.plan.template"].search([("is_default", "=", True)], limit=1)
        if template:
            for project in self.project_ids.filtered(lambda p: p.date_start and p.date and not p.task_ids):
                project._zc_build_plan(template)
        return res
