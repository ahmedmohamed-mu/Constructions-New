from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class HomVariationOrder(models.Model):
    _name = "hom.variation.order"
    _description = "Variation Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "sale_order_id, sequence_no desc, id desc"
    _check_company_auto = True

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    sequence_no = fields.Integer(readonly=True, copy=False)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True, tracking=True, check_company=True,
                                    domain="[('state', '=', 'sale'), ('hom_project_type', '=', 'contracting')]")
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id", store=True)
    partner_id = fields.Many2one(related="sale_order_id.partner_id", store=True, string="Owner")
    project_id = fields.Many2one("project.project", compute="_compute_project", store=True)
    analytic_account_id = fields.Many2one(related="project_id.account_id", string="Project Analytic")
    title = fields.Char(required=True, tracking=True, help="e.g. Add FM-200 system to the server room")
    reason = fields.Selection([
        ("owner", "Owner request"), ("consultant", "Consultant instruction"),
        ("design", "Design change"), ("site", "Site condition"), ("other", "Other"),
    ], default="owner", required=True)
    reference = fields.Char(string="Instruction Ref.", help="Consultant / owner instruction number")
    date = fields.Date(default=fields.Date.context_today, required=True)
    requested_by_id = fields.Many2one("res.users", default=lambda self: self.env.user, tracking=True)
    approver_id = fields.Many2one("res.users", string="Approver", tracking=True,
                                  default=lambda self: self.env.company.hom_vo_approver_id)
    state = fields.Selection([
        ("draft", "Draft"), ("submitted", "Waiting Approval"), ("approved", "Approved"),
        ("applied", "Applied"), ("rejected", "Rejected"),
    ], default="draft", required=True, tracking=True, copy=False)
    line_ids = fields.One2many("hom.variation.order.line", "variation_id", copy=True)
    cost_line_ids = fields.One2many("hom.variation.order.cost", "variation_id", copy=True)
    amount_total = fields.Monetary(string="Contract Impact", compute="_compute_totals", store=True)
    cost_total = fields.Monetary(string="Budget Impact", compute="_compute_totals", store=True)
    margin = fields.Monetary(compute="_compute_totals", store=True)
    # Budget is an optional (Enterprise) app: keep a plain id instead of a hard dependency.
    budget_id = fields.Integer(string="Revised Budget ID", readonly=True, copy=False)

    @api.depends("sale_order_id")
    def _compute_project(self):
        for vo in self:
            vo.project_id = vo.sale_order_id.project_ids[:1]

    @api.depends("line_ids.amount", "cost_line_ids.amount")
    def _compute_totals(self):
        for vo in self:
            vo.amount_total = sum(vo.line_ids.mapped("amount"))
            vo.cost_total = sum(vo.cost_line_ids.mapped("amount"))
            vo.margin = vo.amount_total - vo.cost_total

    def action_submit(self):
        for vo in self.filtered(lambda v: v.state == "draft"):
            if not vo.line_ids:
                raise UserError(_("Add at least one contract line to the variation order."))
            if not vo.sequence_no:
                last = self.search([("sale_order_id", "=", vo.sale_order_id.id), ("sequence_no", ">", 0)],
                                   order="sequence_no desc", limit=1)
                vo.sequence_no = (last.sequence_no or 0) + 1
                vo.name = "VO-%02d" % vo.sequence_no
            vo.state = "submitted"
            if vo.approver_id:
                vo.activity_schedule("mail.mail_activity_data_todo", user_id=vo.approver_id.id,
                                     summary=_("Approve %(vo)s on %(so)s", vo=vo.name, so=vo.sale_order_id.name),
                                     note=_("Contract impact %(a)s, budget impact %(c)s.",
                                            a=vo.amount_total, c=vo.cost_total))

    def _check_approver(self):
        if self.approver_id and self.approver_id != self.env.user and not self.env.is_superuser() \
                and not self.env.user.has_group("sales_team.group_sale_manager"):
            raise UserError(_("Only %s can approve this variation order.", self.approver_id.name))

    def action_approve(self):
        for vo in self.filtered(lambda v: v.state == "submitted"):
            vo._check_approver()
            vo.state = "approved"
            vo.activity_feedback(["mail.mail_activity_data_todo"])
            vo._apply()

    def action_reject(self):
        for vo in self.filtered(lambda v: v.state == "submitted"):
            vo._check_approver()
            vo.state = "rejected"
            vo.activity_unlink(["mail.mail_activity_data_todo"])

    def action_reset_draft(self):
        self.filtered(lambda v: v.state in ("submitted", "rejected")).state = "draft"

    # ------------------------------------------------------------------
    # Apply to contract and budget
    # ------------------------------------------------------------------
    def _apply(self):
        self.ensure_one()
        order = self.sale_order_id
        new_lines = self.line_ids.filtered(lambda l: l.line_type == "add")
        cmds = []
        if new_lines:
            seq = max(order.order_line.mapped("sequence") or [0]) + 1
            cmds.append(Command.create({"display_type": "line_section", "sequence": seq,
                                        "name": _("Variation Order %(vo)s - %(t)s", vo=self.name, t=self.title)}))
            for line in new_lines:
                seq += 1
                cmds.append(Command.create({
                    "sequence": seq, "product_id": line.product_id.id, "name": line.name or line.product_id.display_name,
                    "product_uom_qty": line.quantity, "price_unit": line.price_unit,
                }))
        for line in self.line_ids.filtered(lambda l: l.line_type == "change"):
            sol = line.sale_line_id
            if sol.qty_invoiced > sol.product_uom_qty + line.quantity:
                raise UserError(_("%s: the new quantity is lower than what is already invoiced.", sol.product_id.display_name))
            cmds.append(Command.update(sol.id, {"product_uom_qty": sol.product_uom_qty + line.quantity,
                                                "price_unit": sol.price_unit, "discount": sol.discount}))
        if cmds:
            order.write({"order_line": cmds})
        order.message_post(body=_("%(vo)s applied: %(t)s (contract impact %(a)s).",
                                  vo=self.name, t=self.title, a=self.amount_total))
        self._apply_budget()
        self.state = "applied"

    def _apply_budget(self):
        """Revise the confirmed cost budget of the project (only when the Budget app is installed)."""
        self.ensure_one()
        if "budget.analytic" not in self.env or not self.cost_line_ids or not self.analytic_account_id:
            return
        project_account = self.analytic_account_id
        project_col = project_account.plan_id._column_name()
        Budget = self.env["budget.analytic"]
        budget = Budget.search([
            ("state", "=", "confirmed"),
            ("budget_line_ids." + project_col, "=", project_account.id),
        ], order="id desc", limit=1)
        if not budget:
            self.message_post(body=_("No confirmed budget found for %s: budget not revised.", project_account.display_name))
            return
        before = set(Budget.search([]).ids)
        budget.create_revised_budget()
        revision = Budget.search([("id", "not in", list(before))], order="id desc", limit=1) or budget
        for cost in self.cost_line_ids:
            col = cost.analytic_account_id.plan_id._column_name()
            line = revision.budget_line_ids.filtered(
                lambda l: l[project_col] == project_account and (col == project_col or l[col] == cost.analytic_account_id))[:1]
            if line:
                line.budget_amount += cost.amount
            else:
                vals = {"budget_analytic_id": revision.id, project_col: project_account.id, "budget_amount": cost.amount}
                if col != project_col:
                    vals[col] = cost.analytic_account_id.id
                self.env["budget.line"].create(vals)
        if revision.state == "draft" and hasattr(revision, "action_budget_confirm"):
            revision.action_budget_confirm()
        self.budget_id = revision.id
        revision.message_post(body=_("Revised by variation order %(vo)s on %(so)s.", vo=self.name, so=self.sale_order_id.name))

    def action_view_budget(self):
        self.ensure_one()
        return self.env["budget.analytic"].browse(self.budget_id)._get_records_action()


class HomVariationOrderLine(models.Model):
    _name = "hom.variation.order.line"
    _description = "Variation Order - Contract Line"

    variation_id = fields.Many2one("hom.variation.order", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="variation_id.currency_id")
    line_type = fields.Selection([("add", "New item"), ("change", "Quantity change")], default="add", required=True)
    sale_line_id = fields.Many2one("sale.order.line", string="Existing Item",
                                   domain="[('order_id', '=', parent.sale_order_id), ('display_type', '=', False), ('is_downpayment', '=', False)]")
    product_id = fields.Many2one("product.product", domain="[('sale_ok', '=', True)]")
    name = fields.Char(string="Description")
    quantity = fields.Float(string="Qty (+/-)", default=1.0, digits="Product Unit")
    price_unit = fields.Float(digits="Product Price")
    amount = fields.Monetary(compute="_compute_amount", store=True)

    @api.onchange("sale_line_id")
    def _onchange_sale_line(self):
        if self.sale_line_id:
            self.product_id = self.sale_line_id.product_id
            self.price_unit = self.sale_line_id.price_unit
            self.name = self.sale_line_id.name

    @api.onchange("product_id")
    def _onchange_product(self):
        if self.product_id and self.line_type == "add" and not self.price_unit:
            self.price_unit = self.product_id.lst_price

    @api.depends("quantity", "price_unit")
    def _compute_amount(self):
        for line in self:
            line.amount = line.quantity * line.price_unit

    @api.constrains("line_type", "sale_line_id", "product_id")
    def _check_line(self):
        for line in self:
            if line.line_type == "change" and not line.sale_line_id:
                raise UserError(_("Select the contract item whose quantity changes."))
            if line.line_type == "add" and not line.product_id:
                raise UserError(_("Select the product of the new item."))


class HomVariationOrderCost(models.Model):
    _name = "hom.variation.order.cost"
    _description = "Variation Order - Budget Impact"

    variation_id = fields.Many2one("hom.variation.order", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="variation_id.currency_id")
    analytic_account_id = fields.Many2one("account.analytic.account", string="Trade / Analytic", required=True)
    name = fields.Char(string="Description")
    amount = fields.Monetary(string="Cost Change (+/-)")


class ResCompany(models.Model):
    _inherit = "res.company"

    hom_vo_approver_id = fields.Many2one("res.users", string="Variation Orders Approver")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    hom_vo_approver_id = fields.Many2one(related="company_id.hom_vo_approver_id", readonly=False)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    hom_variation_ids = fields.One2many("hom.variation.order", "sale_order_id")
    hom_variation_count = fields.Integer(compute="_compute_hom_variation_count")

    def _compute_hom_variation_count(self):
        for order in self:
            order.hom_variation_count = len(order.hom_variation_ids)

    def action_hom_variations(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("hom_variation_order.action_hom_variation_order")
        action["domain"] = [("sale_order_id", "=", self.id)]
        action["context"] = {"default_sale_order_id": self.id}
        return action
