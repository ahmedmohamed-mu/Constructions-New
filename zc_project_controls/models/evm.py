from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from .tools import analytic_amount, month_end, month_start, months_between, project_order


class ZcBaseline(models.Model):
    """Approved time-phased budget of a contract: the BOQ value spread over the months of the programme.

    Earned value is measured against the approved version; a new version (after a variation order or an
    approved extension of time) supersedes it without changing history.
    """
    _name = "zc.baseline"
    _description = "Project Baseline (time-phased BOQ)"
    _inherit = ["mail.thread"]
    _order = "sale_order_id, version desc"

    name = fields.Char(compute="_compute_name", store=True)
    sale_order_id = fields.Many2one("sale.order", string="Contract", required=True, tracking=True,
                                    domain="[('state', '=', 'sale'), ('hom_project_type', '=', 'contracting')]")
    project_id = fields.Many2one("project.project", compute="_compute_project", store=True)
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    currency_id = fields.Many2one(related="sale_order_id.currency_id")
    version = fields.Integer(default=1, readonly=True, copy=False)
    state = fields.Selection([("draft", "Draft"), ("approved", "Approved"), ("superseded", "Superseded")],
                             default="draft", required=True, tracking=True, copy=False)
    date_start = fields.Date(string="Programme Start", required=True, tracking=True)
    date_end = fields.Date(string="Programme Finish", required=True, tracking=True)
    approved_date = fields.Date(readonly=True, copy=False)
    approved_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    item_ids = fields.One2many("zc.baseline.item", "baseline_id", string="BOQ Items", copy=True)
    line_ids = fields.One2many("zc.baseline.line", "baseline_id", string="Monthly Planned Value", copy=False)
    amount_total = fields.Monetary(string="Budget at Completion (BAC)", compute="_compute_total", store=True)
    note = fields.Text()

    @api.depends("sale_order_id.name", "version")
    def _compute_name(self):
        for bl in self:
            bl.name = "%s - BL%02d" % (bl.sale_order_id.name or "", bl.version)

    @api.depends("sale_order_id")
    def _compute_project(self):
        for bl in self:
            bl.project_id = bl.sale_order_id.project_ids[:1]

    @api.depends("line_ids.planned_value")
    def _compute_total(self):
        for bl in self:
            bl.amount_total = sum(bl.line_ids.mapped("planned_value"))

    @api.onchange("sale_order_id")
    def _onchange_sale_order(self):
        project = self.sale_order_id.project_ids[:1]
        if project:
            self.date_start = self.date_start or project.date_start
            self.date_end = self.date_end or project.date

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for bl in self:
            if bl.date_end < bl.date_start:
                raise UserError(_("The programme finish is before its start."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("sale_order_id") and not vals.get("version"):
                last = self.search([("sale_order_id", "=", vals["sale_order_id"])], order="version desc", limit=1)
                vals["version"] = (last.version or 0) + 1
        return super().create(vals_list)

    def action_load_items(self):
        """One row per BOQ item, spread over the whole programme by default (edit dates / curve per item)."""
        for bl in self:
            if bl.state != "draft":
                raise UserError(_("Only a draft baseline can be changed."))
            existing = bl.item_ids.sale_line_id
            lines = bl.sale_order_id.order_line.filtered(
                lambda l: not l.display_type and not l.is_downpayment and l.product_id
                and l.product_uom_qty > 0 and l not in existing
                and not ("zc_provisional" in l._fields and l.zc_provisional))
            bl.item_ids = [Command.create({"sale_line_id": l.id, "date_start": bl.date_start,
                                           "date_end": bl.date_end}) for l in lines]
        return True

    def action_generate(self):
        for bl in self:
            if bl.state != "draft":
                raise UserError(_("Only a draft baseline can be changed."))
            if not bl.item_ids:
                bl.action_load_items()
            bl.line_ids.unlink()
            vals = []
            currency = bl.currency_id
            for item in bl.item_ids:
                dist = item._distribution()
                done = 0.0
                for i, (month, share) in enumerate(dist):
                    # rounded per month, the last month takes the rest so the item adds up to its value exactly
                    value = item.amount - done if i == len(dist) - 1 else currency.round(item.amount * share)
                    done += value
                    vals.append({"baseline_id": bl.id, "item_id": item.id, "sale_line_id": item.sale_line_id.id,
                                 "month": month, "planned_qty": item.quantity * share, "planned_value": value})
            self.env["zc.baseline.line"].create(vals)
        return True

    def action_approve(self):
        for bl in self:
            if bl.state != "draft":
                continue
            if not bl.line_ids:
                bl.action_generate()
            previous = self.search([("sale_order_id", "=", bl.sale_order_id.id), ("state", "=", "approved"),
                                    ("id", "!=", bl.id)])
            previous.write({"state": "superseded"})
            bl.write({"state": "approved", "approved_date": fields.Date.context_today(self),
                      "approved_by_id": self.env.user.id})
            bl.message_post(body=_("Baseline approved: BAC %s.", bl.amount_total))
        return True

    def action_revise(self):
        """New draft version (e.g. after a variation order); the approved one stays until the new one is approved."""
        self.ensure_one()
        new = self.copy({"version": 0, "state": "draft"})
        new.action_load_items()
        new.action_generate()
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": new.id, "view_mode": "form"}

    def action_view_curve(self):
        self.ensure_one()
        return self.env["zc.evm.report"].with_context(zc_project_ids=self.project_id.ids).action_refresh()


class ZcBaselineItem(models.Model):
    _name = "zc.baseline.item"
    _description = "Baseline - BOQ Item Schedule"
    _order = "baseline_id, sequence, id"

    baseline_id = fields.Many2one("zc.baseline", required=True, ondelete="cascade", index=True)
    currency_id = fields.Many2one(related="baseline_id.currency_id")
    sale_line_id = fields.Many2one("sale.order.line", string="BOQ Item", required=True)
    sequence = fields.Integer(related="sale_line_id.sequence", store=True)
    product_id = fields.Many2one(related="sale_line_id.product_id")
    quantity = fields.Float(related="sale_line_id.product_uom_qty", string="Contract Qty")
    price_unit = fields.Float(compute="_compute_amount", digits="Product Price")
    amount = fields.Monetary(string="Item Value", compute="_compute_amount")
    date_start = fields.Date(string="Planned Start", required=True)
    date_end = fields.Date(string="Planned Finish", required=True)
    curve = fields.Selection([("linear", "Linear"), ("front", "Front loaded"), ("back", "Back loaded"),
                              ("bell", "Bell (S-curve)")], default="linear", required=True)

    @api.depends("sale_line_id.price_unit", "sale_line_id.discount", "sale_line_id.product_uom_qty")
    def _compute_amount(self):
        for item in self:
            line = item.sale_line_id
            item.price_unit = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            item.amount = item.price_unit * line.product_uom_qty

    def _distribution(self):
        """[(first day of month, share)] summing to 1: days of the item window in each month x curve weight."""
        self.ensure_one()
        start, end = self.date_start, self.date_end
        if not start or not end or end < start:
            return []
        months = months_between(start, end)
        n = len(months)
        weights = []
        for i, m in enumerate(months):
            days = (min(month_end(m), end) - max(m, start)).days + 1
            factor = {"linear": 1.0, "front": float(n - i), "back": float(i + 1),
                      "bell": float(min(i + 1, n - i))}[self.curve]
            weights.append(days * factor)
        total = sum(weights) or 1.0
        return [(m, w / total) for m, w in zip(months, weights)]


class ZcBaselineLine(models.Model):
    _name = "zc.baseline.line"
    _description = "Baseline - Planned Value per Month"
    _order = "baseline_id, month, id"

    baseline_id = fields.Many2one("zc.baseline", required=True, ondelete="cascade", index=True)
    item_id = fields.Many2one("zc.baseline.item", ondelete="cascade")
    sale_line_id = fields.Many2one("sale.order.line", string="BOQ Item")
    project_id = fields.Many2one(related="baseline_id.project_id", store=True)
    currency_id = fields.Many2one(related="baseline_id.currency_id")
    month = fields.Date(required=True)
    planned_qty = fields.Float(digits="Product Unit")
    planned_value = fields.Monetary(string="Planned Value (PV)")


class ZcEvmReport(models.Model):
    """Earned value status per project, rebuilt from the documents each time it is opened."""
    _name = "zc.evm.report"
    _description = "Earned Value Status"
    _order = "spi, project_id"

    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    project_id = fields.Many2one("project.project", readonly=True)
    sale_order_id = fields.Many2one("sale.order", string="Contract", readonly=True)
    baseline_id = fields.Many2one("zc.baseline", readonly=True)
    date = fields.Date(string="Status Date", readonly=True)
    bac = fields.Monetary(string="BAC", readonly=True, help="Budget at completion (baseline value).")
    pv = fields.Monetary(string="Planned (PV)", readonly=True)
    ev = fields.Monetary(string="Earned (EV)", readonly=True,
                         help="Certified physical progress x full contract rate.")
    ev_billed = fields.Monetary(string="Certified Value", readonly=True,
                                help="Cumulative value of the certificates (with stage valuation when used).")
    ac = fields.Monetary(string="Actual Cost (AC)", readonly=True)
    sv = fields.Monetary(string="Schedule Variance", readonly=True)
    cv = fields.Monetary(string="Cost Variance", readonly=True)
    spi = fields.Float(string="SPI", readonly=True, digits=(16, 2), aggregator="avg")
    cpi = fields.Float(string="CPI", readonly=True, digits=(16, 2), aggregator="avg")
    eac = fields.Monetary(string="Estimate at Completion", readonly=True)
    planned_pct = fields.Float(string="Planned %", readonly=True, digits=(16, 1))
    earned_pct = fields.Float(string="Earned %", readonly=True, digits=(16, 1))
    status = fields.Selection([("on_track", "On track"), ("watch", "Watch"), ("critical", "Critical")],
                              readonly=True)

    # ------------------------------------------------------------------
    @api.model
    def _baselines(self, project_ids=None):
        domain = [("state", "=", "approved")]
        if project_ids:
            domain.append(("project_id", "in", project_ids))
        return self.env["zc.baseline"].sudo().search(domain)

    @api.model
    def _pv_at(self, baseline, day):
        """Planned value at a date: full months before it + the elapsed share of its month."""
        current = month_start(day)
        total = 0.0
        for line in baseline.line_ids:
            if line.month < current:
                total += line.planned_value
            elif line.month == current:
                total += line.planned_value * day.day / month_end(day).day
        return total

    @api.model
    def _certificate_at(self, order, day):
        certs = self.env["hom.progress.certificate"].sudo().search(
            [("sale_order_id", "=", order.id), ("state", "in", ("approved", "invoiced"))])
        certs = certs.filtered(lambda c: (c.period_end or c.date) <= day)
        return certs.sorted(lambda c: (c.period_end or c.date, c.id))[-1:] if certs else certs

    @api.model
    def _ev_at(self, order, day):
        """(EV at full rate on certified physical progress, cumulative certified value)."""
        cert = self._certificate_at(order, day)
        if not cert:
            return 0.0, 0.0
        cert_day = cert.period_end or cert.date
        ev = 0.0
        for line in cert.line_ids:
            sol = line.sale_line_id
            price = sol.price_unit * (1 - (sol.discount or 0.0) / 100.0)
            if order.zc_stage_valuation:
                installed, _supplied = sol._zc_measured(cert_day)
                qty = min(installed, sol.product_uom_qty) if sol.product_uom_qty else installed
                qty = max(qty, 0.0)
            else:
                qty = line.cumulative_qty
            ev += qty * price
        return ev, cert.amount_cumulative

    @api.model
    def _ac_at(self, project, day):
        return analytic_amount(self.env, project.account_id, date_to=day, sign="cost")

    @api.model
    def _status_row(self, baseline, day):
        order, project = baseline.sale_order_id, baseline.project_id
        pv = self._pv_at(baseline, day)
        ev, billed = self._ev_at(order, day)
        ac = self._ac_at(project, day)
        bac = baseline.amount_total
        spi = ev / pv if pv else 0.0
        cpi = ev / ac if ac else 0.0
        eac = ac + (bac - ev) / cpi if cpi else bac
        worst = min(x for x in (spi or 1.0, cpi or 1.0))
        status = "on_track" if worst >= 0.95 else "watch" if worst >= 0.85 else "critical"
        if not pv and not ev:
            status = "on_track"
        return {
            "company_id": baseline.company_id.id, "project_id": project.id, "sale_order_id": order.id,
            "baseline_id": baseline.id, "date": day, "bac": bac, "pv": pv, "ev": ev, "ev_billed": billed,
            "ac": ac, "sv": ev - pv, "cv": ev - ac, "spi": spi, "cpi": cpi, "eac": eac,
            "planned_pct": pv / bac * 100.0 if bac else 0.0, "earned_pct": ev / bac * 100.0 if bac else 0.0,
            "status": status,
        }

    @api.model
    def _zc_compute(self, project_ids=None, day=None):
        day = day or fields.Date.context_today(self)
        return [self._status_row(bl, day) for bl in self._baselines(project_ids)]

    @api.model
    def _zc_curve(self, project_ids=None, day=None):
        """Cumulative PV / EV / AC per month-end; EV and AC only up to today."""
        day = day or fields.Date.context_today(self)
        rows = []
        for bl in self._baselines(project_ids):
            order, project = bl.sale_order_id, bl.project_id
            last = max([bl.date_end, day])
            for m in months_between(bl.date_start, last):
                end = month_end(m)
                cut = min(end, day)
                pv = sum(l.planned_value for l in bl.line_ids if l.month <= m)
                base = {"company_id": bl.company_id.id, "project_id": project.id, "month": m}
                rows.append(dict(base, series="pv", value=pv))
                if m <= day:
                    ev, _billed = self._ev_at(order, cut)
                    rows.append(dict(base, series="ev", value=ev))
                    rows.append(dict(base, series="ac", value=self._ac_at(project, cut)))
        return rows

    @api.model
    def action_refresh(self):
        project_ids = self.env.context.get("zc_project_ids")
        rows = self._zc_compute(project_ids)
        Curve = self.env["zc.evm.curve"].sudo()
        self.sudo().search([]).unlink()
        Curve.search([]).unlink()
        self.sudo().create(rows)
        Curve.create(self._zc_curve(project_ids))
        return {
            "type": "ir.actions.act_window", "name": _("Earned Value"), "res_model": self._name,
            "views": [[self.env.ref("zc_project_controls.zc_evm_report_list").id, "list"], [False, "form"]],
            "domain": [("project_id", "in", project_ids)] if project_ids else [],
            "target": "current",
        }

    def action_open_curve(self):
        action = self.env["ir.actions.act_window"]._for_xml_id("zc_project_controls.action_zc_evm_curve")
        if self:
            action["domain"] = [("project_id", "in", self.project_id.ids)]
        return action


class ZcEvmCurve(models.Model):
    _name = "zc.evm.curve"
    _description = "Earned Value S-curve"
    _order = "project_id, month, series"

    company_id = fields.Many2one("res.company", readonly=True)
    currency_id = fields.Many2one(related="company_id.currency_id")
    project_id = fields.Many2one("project.project", readonly=True)
    month = fields.Date(readonly=True)
    series = fields.Selection([("pv", "Planned (PV)"), ("ev", "Earned (EV)"), ("ac", "Actual Cost (AC)")],
                              readonly=True)
    value = fields.Monetary(string="Cumulative Value", readonly=True, aggregator="sum")
