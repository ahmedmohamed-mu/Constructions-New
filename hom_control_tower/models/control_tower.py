from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError


def _act(name, model, domain, views=("list", "form"), context=None):
    return {
        "type": "ir.actions.act_window", "name": name, "res_model": model, "domain": domain,
        "views": [[False, v] for v in views], "target": "current", "context": context or {},
    }


class HomControlTower(models.AbstractModel):
    """Read-only aggregation behind the Contracting Control Tower dashboards."""
    _name = "hom.control.tower"
    _description = "Contracting Control Tower"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    def _check_access(self):
        user = self.env.user
        groups = ("base.group_system", "sales_team.group_sale_manager", "account.group_account_manager",
                  "project.group_project_manager", "purchase.group_purchase_manager")
        if not any(user.has_group(g) for g in groups):
            raise AccessError(_("The Control Tower is reserved to managers."))

    @api.model
    def get_dashboard_data(self):
        self._check_access()
        env = self.sudo().env
        company = self.env.company
        today = fields.Date.context_today(self)
        ctx = {
            "today": fields.Date.to_string(today),
            "company": company.name,
            "currency": {"symbol": company.currency_id.symbol, "position": company.currency_id.position,
                         "name": company.currency_id.name},
        }
        orders = env["sale.order"].search([("state", "=", "sale"), ("hom_project_type", "=", "contracting"),
                                           ("company_id", "=", company.id)])
        projects = [self._project_row(o, today) for o in orders]
        projects.sort(key=lambda p: -p["contract"])
        ctx["projects"] = projects
        ctx["executive"] = self._executive(orders, projects, today)
        ctx["finance"] = self._finance(company, today)
        ctx["fm"] = self._fm(company, today)
        ctx["alerts"] = self._alerts(company, today)
        return ctx

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _project_of(self, order):
        return order.project_ids[:1] if "project_ids" in order._fields else self.env["project.project"]

    def _analytic_ids(self, record_lines):
        ids = set()
        for line in record_lines:
            for key in (line.analytic_distribution or {}):
                ids.update(int(a) for a in str(key).split(","))
        return ids

    def _subcontracts_of(self, project):
        POs = self.sudo().env["purchase.order"]
        if not project or not project.account_id:
            return POs
        subs = POs.search([("hom_is_subcontract", "=", True), ("state", "=", "purchase")])
        return subs.filtered(lambda po: project.account_id.id in self._analytic_ids(po.order_line))

    def _work_invoiced(self, order):
        retention = order.company_id.hom_retention_owner_product_id
        total = 0.0
        for move in order.invoice_ids.filtered(lambda m: m.state == "posted"):
            sign = -1 if move.move_type == "out_refund" else 1
            lines = move.invoice_line_ids.filtered(
                lambda l: l.display_type == "product" and not l.is_downpayment and l.product_id != retention)
            total += sign * sum(lines.mapped("price_subtotal"))
        return total

    _SETTLED = ("in_payment", "paid")

    def _collections(self, moves):
        """(date, amount) of the money received/paid on the given invoices.

        Reconciled settlements come from the partial reconciliations; payments registered without a journal
        entry (Odoo 18+ "in payment" flow, waiting for the bank statement) come from account.payment.
        """
        env = self.sudo().env
        moves = moves.filtered(lambda m: m.state == "posted")
        out = []
        for move in moves:
            for line in move.line_ids.filtered(
                    lambda l: l.account_id.account_type in ("asset_receivable", "liability_payable")):
                for partial in line.matched_credit_ids | line.matched_debit_ids:
                    out.append((partial.max_date, partial.amount))
        if moves and "invoice_ids" in env["account.payment"]._fields:
            pays = env["account.payment"].search([("invoice_ids", "in", moves.ids), ("move_id", "=", False),
                                                  ("state", "not in", ("draft", "canceled", "rejected"))])
            for pay in pays:
                linked = pay.invoice_ids & moves
                total = sum(linked.mapped("amount_total")) or 1.0
                for inv in linked:
                    out.append((pay.date, pay.amount * inv.amount_total / total))
        return out

    def _open_amount(self, moves):
        return sum(m.amount_residual_signed for m in moves if m.state == "posted" and m.payment_state not in self._SETTLED)

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------
    def _project_row(self, order, today):
        env = self.sudo().env
        project = self._project_of(order)
        contract = order.amount_untaxed
        figures = project._hom_contract_figures() if project and hasattr(project, "_hom_contract_figures") else {}
        invoices = order.invoice_ids.filtered(lambda m: m.state == "posted" and m.move_type in ("out_invoice", "out_refund"))
        collected = sum(a for _d, a in self._collections(invoices))
        ar_open = self._open_amount(invoices)
        margin = figures.get("margin")
        status = "on_track" if margin is None or margin >= 15 else ("at_risk" if margin >= 5 else "off_track")
        # time elapsed
        elapsed = None
        if project and project.date_start and project.date and project.date > project.date_start:
            span = (project.date - project.date_start).days
            elapsed = max(0.0, min(100.0, (today - project.date_start).days / span * 100.0))
        # subcontracts
        subs = []
        for po in self._subcontracts_of(project):
            certs = env["hom.subcontract.certificate"].search([("purchase_order_id", "=", po.id),
                                                               ("state", "in", ("approved", "billed"))])
            last = certs.sorted("number")[-1:]
            subs.append({
                "id": po.id, "name": po.name, "partner": po.partner_id.name, "amount": po.amount_untaxed,
                "progress": last.sub_progress_pct if last else 0.0,
                "warning": bool(last.back_to_back_warning) if last else False,
                "advance_remaining": po.hom_advance_remaining, "retention": po.hom_retention_balance,
            })
        # budget by trade
        budget = self._budget_rows(project)
        # variation orders
        vos = env["hom.variation.order"].search([("sale_order_id", "=", order.id)])
        # measurements / certificates waiting
        open_meas = env["hom.site.measurement"].search_count([("sale_order_id", "=", order.id), ("state", "=", "open")])
        waiting = env["hom.progress.certificate"].search_count([("sale_order_id", "=", order.id), ("state", "=", "submitted")])
        blocked = 0
        Daily = env.get("x_site_daily_report")
        if Daily is not None and project and "x_studio_status" in Daily._fields:
            blocked = Daily.search_count([("x_studio_project", "=", project.id), ("x_studio_status", "=", "blocked")])
        sub_open = sub_late = 0
        if "hom.submittal" in env and project:
            Sub = env["hom.submittal"]
            sub_open = Sub.search_count([("project_id", "=", project.id), ("state", "=", "submitted")])
            sub_late = Sub.search_count([("project_id", "=", project.id), ("is_overdue", "=", True)])
        return {
            "submittals_open": sub_open, "submittals_late": sub_late,
            "id": order.id, "project_id": project.id or False,
            "name": project.name or order.name, "order": order.name,
            "partner": order.partner_id.commercial_partner_id.name,
            "pm": project.user_id.name or "", "consultant": order.hom_consultant_id.name or "",
            "contract": contract,
            "certified": order.hom_certified_amount, "certified_pct": order.hom_certified_pct,
            "invoiced": self._work_invoiced(order), "collected": collected, "ar_open": ar_open,
            "advance_outstanding": order.hom_advance_outstanding, "retention": order.hom_retention_balance,
            "actual_cost": figures.get("actual_cost", 0.0), "etc": figures.get("etc", 0.0),
            "eac": figures.get("eac", 0.0), "margin": margin, "status": status,
            "elapsed_pct": elapsed, "date_start": fields.Date.to_string(project.date_start) if project and project.date_start else False,
            "date_end": fields.Date.to_string(project.date) if project and project.date else False,
            "subcontracts": subs, "budget": budget,
            "vo_count": len(vos), "vo_amount": sum(vos.filtered(lambda v: v.state == "applied").mapped("amount_total")),
            "vo_pending": len(vos.filtered(lambda v: v.state == "submitted")),
            "open_measurements": open_meas, "waiting_consultant": waiting, "blocked_reports": blocked,
        }

    def _budget_rows(self, project):
        env = self.sudo().env
        if "budget.line" not in env or not project or not project.account_id:
            return []
        Line = env["budget.line"]
        cols = [n for n, f in Line._fields.items() if f.type == "many2one" and f.comodel_name == "account.analytic.account"
                and f.store and n != "auto_account_id"]
        lines = Line.search([("budget_analytic_id.state", "=", "confirmed"),
                             ("budget_analytic_id.budget_type", "!=", "revenue")])
        rows = defaultdict(lambda: {"budget": 0.0, "committed": 0.0, "achieved": 0.0})
        for bl in lines:
            accounts = [bl[c] for c in cols if bl[c]]
            if project.account_id not in accounts:
                continue
            label = " / ".join(a.name for a in accounts if a != project.account_id) or _("Project")
            r = rows[label]
            r["budget"] += bl.budget_amount
            r["committed"] += bl.committed_amount
            r["achieved"] += bl.achieved_amount
        return [dict(trade=k, **v) for k, v in sorted(rows.items(), key=lambda kv: -kv[1]["budget"])]

    # ------------------------------------------------------------------
    # Executive
    # ------------------------------------------------------------------
    def _executive(self, orders, projects, today):
        env = self.sudo().env
        kpi = {k: sum(p[k] for p in projects) for k in
               ("contract", "certified", "invoiced", "collected", "ar_open", "advance_outstanding", "retention",
                "actual_cost", "eac")}
        kpi["projects"] = len(projects)
        kpi["certified_pct"] = (kpi["certified"] / kpi["contract"] * 100.0) if kpi["contract"] else 0.0
        with_margin = [p for p in projects if p["margin"] is not None]
        kpi["margin"] = (sum(p["contract"] - p["eac"] for p in with_margin) / sum(p["contract"] for p in with_margin) * 100.0) \
            if with_margin and sum(p["contract"] for p in with_margin) else None
        subs = env["purchase.order"].search([("hom_is_subcontract", "=", True), ("state", "=", "purchase")])
        kpi["sub_committed"] = sum(subs.mapped("amount_untaxed"))
        G = env["hom.bank.guarantee"].search([("state", "in", ("active", "extended")), ("direction", "=", "issued")])
        kpi["guarantees_issued"] = sum(G.mapped("amount_current"))
        # pipeline (open opportunities)
        pipeline = 0.0
        if "crm.lead" in env:
            leads = env["crm.lead"].search([("type", "=", "opportunity"), ("active", "=", True),
                                            ("stage_id.is_won", "=", False)])
            pipeline = sum(leads.mapped("expected_revenue"))
        kpi["pipeline"] = pipeline
        return {"kpi": kpi, "series": self._monthly_series(orders, today)}

    def _monthly_series(self, orders, today):
        env = self.sudo().env
        buckets = defaultdict(lambda: {"certified": 0.0, "invoiced": 0.0, "collected": 0.0})
        certs = env["hom.progress.certificate"].search([("sale_order_id", "in", orders.ids),
                                                        ("state", "in", ("approved", "invoiced"))])
        for c in certs:
            buckets[c.date.strftime("%Y-%m")]["certified"] += c.amount_current
        retention = self.env.company.hom_retention_owner_product_id
        moves = orders.invoice_ids.filtered(lambda m: m.state == "posted")
        for m in moves:
            sign = -1 if m.move_type == "out_refund" else 1
            work = m.invoice_line_ids.filtered(
                lambda l: l.display_type == "product" and not l.is_downpayment and l.product_id != retention)
            if m.invoice_date:
                buckets[m.invoice_date.strftime("%Y-%m")]["invoiced"] += sign * sum(work.mapped("price_subtotal"))
        for d, amount in self._collections(moves):
            if d:
                buckets[d.strftime("%Y-%m")]["collected"] += amount
        if not buckets:
            return []
        keys = sorted(buckets)
        start = fields.Date.from_string(keys[0] + "-01")
        end = max(fields.Date.from_string(keys[-1] + "-01"), today.replace(day=1))
        out, cur = [], start
        while cur <= end and len(out) < 12:
            k = cur.strftime("%Y-%m")
            out.append(dict(month=k, **buckets[k]))
            cur += relativedelta(months=1)
        return out[-12:]

    # ------------------------------------------------------------------
    # Finance
    # ------------------------------------------------------------------
    def _aging(self, move_types, today):
        env = self.sudo().env
        moves = env["account.move"].search([("move_type", "in", move_types), ("state", "=", "posted"),
                                            ("amount_residual", "!=", 0), ("company_id", "=", self.env.company.id),
                                            ("payment_state", "not in", self._SETTLED)])
        names = ["not_due", "d30", "d60", "d90", "d90p"]
        b = dict.fromkeys(names, 0.0)
        for m in moves:
            amount = abs(m.amount_residual_signed)
            days = (today - (m.invoice_date_due or m.invoice_date or today)).days
            key = "not_due" if days <= 0 else "d30" if days <= 30 else "d60" if days <= 60 else "d90" if days <= 90 else "d90p"
            b[key] += amount
        b["total"] = sum(b[n] for n in names)
        b["overdue"] = b["total"] - b["not_due"]
        return b

    def _finance(self, company, today):
        env = self.sudo().env
        journals = env["account.journal"].search([("type", "in", ("bank", "cash")), ("company_id", "=", company.id)])
        cash = []
        for j in journals:
            acc = j.default_account_id
            bal = sum(env["account.move.line"].search([("account_id", "=", acc.id), ("parent_state", "=", "posted")]).mapped("balance")) if acc else 0.0
            # payments registered without journal entry, waiting for the bank statement
            pending = env["account.payment"].search([("journal_id", "=", j.id), ("move_id", "=", False),
                                                     ("state", "not in", ("draft", "canceled", "rejected"))])
            bal += sum(p.amount if p.payment_type == "inbound" else -p.amount for p in pending)
            cash.append({"name": j.name, "type": j.type, "balance": bal})
        G = env["hom.bank.guarantee"].search([("state", "in", ("active", "extended", "expired"))], order="expiry_date")
        guarantees = [{
            "id": g.id, "name": g.name, "type": dict(g._fields["guarantee_type"]._description_selection(self.env)).get(g.guarantee_type),
            "direction": g.direction, "partner": g.partner_id.name, "bank": g.bank_name or "",
            "amount": g.amount_current, "expiry": fields.Date.to_string(g.expiry_date), "days": g.days_to_expiry,
            "state": g.state,
        } for g in G]
        banks = defaultdict(float)
        for g in G.filtered(lambda x: x.direction == "issued" and x.state != "expired"):
            banks[g.bank_name or "-"] += g.amount_current
        subs = env["purchase.order"].search([("hom_is_subcontract", "=", True), ("state", "=", "purchase")])
        custody = []
        Employee = env.get("hr.employee")
        if Employee is not None:
            for emp in Employee.search([("work_contact_id", "!=", False)]):
                amls = env["account.move.line"].search([
                    ("partner_id", "=", emp.work_contact_id.id), ("parent_state", "=", "posted"),
                    ("account_id.account_type", "in", ("asset_receivable", "liability_payable")), ("reconciled", "=", False)])
                bal = sum(amls.mapped("amount_residual"))
                if abs(bal) > 1:
                    custody.append({"employee": emp.name, "balance": bal})
        return {
            "cash": cash, "cash_total": sum(c["balance"] for c in cash),
            "ar": self._aging(("out_invoice", "out_refund"), today),
            "ap": self._aging(("in_invoice", "in_refund"), today),
            "guarantees": guarantees,
            "guarantees_issued": sum(g["amount"] for g in guarantees if g["direction"] == "issued" and g["state"] != "expired"),
            "guarantees_received": sum(g["amount"] for g in guarantees if g["direction"] == "received" and g["state"] != "expired"),
            "cash_margin": sum(G.filtered(lambda x: x.state != "expired").mapped("cash_margin")),
            "banks": [{"bank": k, "amount": v} for k, v in sorted(banks.items(), key=lambda kv: -kv[1])],
            "retention_owner": sum(env["sale.order"].search([("hom_project_type", "=", "contracting"), ("state", "=", "sale")]).mapped("hom_retention_balance")),
            "retention_subs": sum(subs.mapped("hom_retention_balance")),
            "advance_owner": sum(env["sale.order"].search([("hom_project_type", "=", "contracting"), ("state", "=", "sale")]).mapped("hom_advance_outstanding")),
            "advance_subs": sum(subs.mapped("hom_advance_remaining")),
            "custody": custody,
        }

    # ------------------------------------------------------------------
    # Facility management
    # ------------------------------------------------------------------
    def _fm(self, company, today):
        env = self.sudo().env
        res = {"available": "helpdesk.ticket" in env}
        fm_orders = env["sale.order"].search([("hom_project_type", "=", "fm"), ("state", "=", "sale")])
        mrr = 0.0
        if "recurring_monthly" in env["sale.order"]._fields:
            mrr = sum(fm_orders.mapped("recurring_monthly"))
        res.update({"contracts": len(fm_orders), "mrr": mrr,
                    "clients": [{"name": o.partner_id.commercial_partner_id.name, "order": o.name,
                                 "monthly": o.recurring_monthly if "recurring_monthly" in o._fields else o.amount_untaxed}
                                for o in fm_orders]})
        res["assets"] = self._fm_assets(company, today)
        if not res["available"]:
            return res
        T = env["helpdesk.ticket"]
        tickets = T.search([("company_id", "=", company.id)])
        open_t = tickets.filtered(lambda t: not t.stage_id.fold)
        closed = tickets - open_t
        late = tickets.filtered(lambda t: ("sla_fail" in t._fields and t.sla_fail) or ("sla_reached_late" in t._fields and t.sla_reached_late))
        prio = dict(T._fields["priority"]._description_selection(self.env))
        now = fields.Datetime.now()
        res.update({
            "open": len(open_t), "closed": len(closed), "late": len(late),
            "sla_pct": round(100.0 * (len(tickets) - len(late)) / len(tickets), 1) if tickets else 100.0,
            "by_priority": [{"priority": prio.get(p, p), "count": len(open_t.filtered(lambda t: t.priority == p))}
                            for p in sorted(prio, reverse=True)],
            "tickets": [{
                "id": t.id, "name": t.name, "partner": t.partner_id.commercial_partner_id.name or "",
                "user": t.user_id.name or "", "priority": prio.get(t.priority, t.priority),
                "deadline": fields.Datetime.to_string(t.sla_deadline) if "sla_deadline" in t._fields and t.sla_deadline else False,
                "hours_left": round((t.sla_deadline - now).total_seconds() / 3600.0, 1) if "sla_deadline" in t._fields and t.sla_deadline else None,
                "stage": t.stage_id.name,
            } for t in open_t.sorted(lambda t: (t.sla_deadline or now + timedelta(days=999)) if "sla_deadline" in t._fields else now)],
        })
        return res

    def _fm_assets(self, company, today):
        """Equipment and preventive maintenance (Maintenance app), when installed."""
        env = self.sudo().env
        if "maintenance.request" not in env:
            return {"available": False}
        R = env["maintenance.request"]
        open_r = R.search([("state", "not in", ("done", "cancelled"))])
        prev = open_r.filtered(lambda r: r.maintenance_type == "preventive")
        week = today + timedelta(days=7)
        late = open_r.filtered(lambda r: r.schedule_date and r.schedule_date.date() < today)
        return {
            "available": True,
            "equipment": env["maintenance.equipment"].search_count([]),
            "open": len(open_r), "preventive": len(prev), "late": len(late),
            "corrective": len(open_r) - len(prev),
            "due_week": len(prev.filtered(lambda r: r.schedule_date and r.schedule_date.date() <= week)),
            "next": [{
                "id": r.id, "name": r.name, "equipment": r.equipment_id.name or "",
                "type": r.maintenance_type, "date": fields.Date.to_string(r.schedule_date.date()) if r.schedule_date else False,
                "late": bool(r.schedule_date and r.schedule_date.date() < today),
                "user": ", ".join(r.user_ids.mapped("name")),
            } for r in open_r.sorted(lambda r: r.schedule_date or fields.Datetime.now() + timedelta(days=999))[:6]],
        }

    # ------------------------------------------------------------------
    # Attention items
    # ------------------------------------------------------------------
    def _alerts(self, company, today):
        env = self.sudo().env
        out = []

        def add(level, text, count, action, amount=None):
            if count:
                out.append({"level": level, "text": text, "count": count, "amount": amount, "action": action})

        limit = today + timedelta(days=30)
        G = env["hom.bank.guarantee"].search([("state", "in", ("active", "extended")), ("expiry_date", "<=", limit)])
        add("danger", _("Letters of guarantee expiring within 30 days"), len(G),
            _act(_("Expiring guarantees"), "hom.bank.guarantee", [("id", "in", G.ids)]), sum(G.mapped("amount_current")))
        B = env["hom.subcontract.certificate"].search([("back_to_back_warning", "=", True), ("state", "in", ("submitted", "approved"))])
        add("danger", _("Subcontractor certificates ahead of the owner's certification"), len(B),
            _act(_("Back-to-back"), "hom.subcontract.certificate", [("id", "in", B.ids)]))
        P = env["purchase.order"].search([("hom_budget_warning", "!=", False), ("state", "=", "purchase")])
        add("warning", _("Purchase orders above the cost budget"), len(P),
            _act(_("Budget overrun"), "purchase.order", [("id", "in", P.ids)]))
        S = env["hom.progress.certificate"].search([("state", "=", "submitted")])
        add("info", _("Owner certificates waiting for the consultant"), len(S),
            _act(_("Waiting consultant"), "hom.progress.certificate", [("id", "in", S.ids)]), sum(S.mapped("amount_net")))
        A = env["hom.progress.certificate"].search([("state", "=", "approved")])
        add("warning", _("Approved certificates not invoiced yet"), len(A),
            _act(_("To invoice"), "hom.progress.certificate", [("id", "in", A.ids)]), sum(A.mapped("amount_net")))
        M = env["hom.site.measurement"].search([("state", "=", "open")])
        add("info", _("Site measurements not certified yet"), len(M),
            _act(_("Site measurements"), "hom.site.measurement", [("id", "in", M.ids)]))
        V = env["hom.variation.order"].search([("state", "=", "submitted")])
        add("warning", _("Variation orders waiting for approval"), len(V),
            _act(_("Variation orders"), "hom.variation.order", [("id", "in", V.ids)]), sum(V.mapped("amount_total")))
        D = env["account.move"].search([("move_type", "=", "out_invoice"), ("state", "=", "posted"),
                                        ("amount_residual", ">", 0), ("invoice_date_due", "<", today),
                                        ("payment_state", "not in", self._SETTLED)])
        add("danger", _("Overdue customer invoices"), len(D),
            _act(_("Overdue invoices"), "account.move", [("id", "in", D.ids)]), sum(D.mapped("amount_residual")))
        if "hom.submittal" in env:
            SB = env["hom.submittal"].search([("is_overdue", "=", True)])
            add("danger", _("Submittals overdue at the consultant"), len(SB),
                _act(_("Overdue submittals"), "hom.submittal", [("id", "in", SB.ids)]))
        if "maintenance.request" in env:
            MR = env["maintenance.request"].search([("state", "not in", ("done", "cancelled")),
                                                    ("schedule_date", "<", fields.Datetime.to_string(today))])
            add("warning", _("Maintenance visits past their planned date"), len(MR),
                _act(_("Late maintenance"), "maintenance.request", [("id", "in", MR.ids)]))
        if "helpdesk.ticket" in env:
            T = env["helpdesk.ticket"].search([("stage_id.fold", "=", False)]).filtered(
                lambda t: ("sla_fail" in t._fields and t.sla_fail))
            add("danger", _("Maintenance tickets out of SLA"), len(T),
                _act(_("Tickets out of SLA"), "helpdesk.ticket", [("id", "in", T.ids)]))
        if "hr.expense" in env:
            X = env["mail.activity"].search([("res_model", "=", "hr.expense"), ("summary", "ilike", "العهدة")])
            add("warning", _("Expenses above the remaining custody"), len(X),
                _act(_("Custody"), "hr.expense", [("id", "in", X.mapped("res_id"))]))
        order = {"danger": 0, "warning": 1, "info": 2}
        out.sort(key=lambda a: order[a["level"]])
        return out

    @api.model
    def get_open_action(self, kind, res_id=False):
        """Actions for clickable cards (kept server-side so menus stay in one place)."""
        if kind == "contract" and res_id:
            return _act(_("Contract"), "sale.order", [("id", "=", res_id)], ("form",)) | {"res_id": res_id}
        mapping = {
            "contracts": ("hom_contracting_base.action_hom_contracts", None),
            "certificates": ("hom_progress_billing.action_hom_progress_certificate", None),
            "sub_certificates": ("hom_subcontract_billing.action_hom_subcontract_certificate", None),
            "guarantees": ("hom_guarantees.action_hom_bank_guarantee", None),
            "measurements": ("hom_progress_billing.action_hom_site_measurement", None),
            "variations": ("hom_variation_order.action_hom_variation_order", None),
            "subcontracts": ("hom_contracting_base.action_hom_subcontracts", None),
        }
        if "hom.submittal" in self.env:
            mapping["submittals"] = ("hom_submittals.action_hom_submittal", None)
        if "maintenance.request" in self.env:
            mapping["maintenance"] = ("maintenance.hr_equipment_request_action", None)
        if kind == "maintenance_request" and res_id:
            return _act(_("Maintenance"), "maintenance.request", [("id", "=", res_id)], ("form",)) | {"res_id": res_id}
        if kind in mapping:
            return self.env["ir.actions.act_window"]._for_xml_id(mapping[kind][0])
        if kind == "receivables":
            return _act(_("Open receivables"), "account.move",
                        [("move_type", "=", "out_invoice"), ("state", "=", "posted"), ("amount_residual", "!=", 0),
                         ("payment_state", "not in", self._SETTLED)])
        if kind == "payables":
            return _act(_("Open payables"), "account.move",
                        [("move_type", "=", "in_invoice"), ("state", "=", "posted"), ("amount_residual", "!=", 0),
                         ("payment_state", "not in", self._SETTLED)])
        if kind == "tickets" and "helpdesk.ticket" in self.env:
            return _act(_("Open tickets"), "helpdesk.ticket", [("stage_id.fold", "=", False)], ("list", "kanban", "form"))
        if kind == "project" and res_id:
            return {"type": "ir.actions.act_window", "res_model": "project.project", "res_id": res_id,
                    "views": [[False, "form"]], "target": "current"}
        return False
