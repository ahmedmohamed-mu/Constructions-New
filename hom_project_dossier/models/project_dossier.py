from collections import defaultdict

from markupsafe import Markup, escape

from odoo import _, api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_hom_dossier(self):
        self.ensure_one()
        project = self.project_ids[:1]
        action = self.env.ref("hom_project_dossier.action_hom_project_dossier_wizard").sudo().read()[0]
        action["context"] = {"active_model": "project.project", "active_id": project.id}
        return action


class ProjectProject(models.Model):
    _inherit = "project.project"

    def action_hom_dossier(self):
        self.ensure_one()
        return self.env.ref("hom_project_dossier.action_report_project_dossier_html").report_action(self)

    # ------------------------------------------------------------------
    # Data of the project dossier: everything comes from the normal work of every user, and every money
    # figure is read from posted accounting (or reconciled to it in the tie-out section).
    # ------------------------------------------------------------------
    def _hom_dossier(self, date_from=False, date_to=False):
        self.ensure_one()
        project = self.sudo()
        env = project.env
        company = project.company_id or env.company
        cur = company.currency_id
        base = project.get_base_url().rstrip("/")
        order = project.reinvoiced_sale_order_id or project.sale_order_id
        account = project.account_id

        def url(rec):
            return "%s/odoo/%s/%s" % (base, rec._name, rec.id) if rec else ""

        def in_period(d):
            if not d:
                return True
            return (not date_from or d >= date_from) and (not date_to or d <= date_to)

        def an_domain(model_name):
            # records whose analytic distribution points to the project account
            return [("analytic_distribution", "in", [account.id])] if account else [("id", "=", 0)]

        def sel(rec, field):
            value = rec[field]
            return dict(rec._fields[field]._description_selection(env)).get(value, value or "")

        data = {"project": project, "order": order, "company": company, "currency": cur, "url": url,
                "date_from": date_from, "date_to": date_to, "sections": []}

        # ---------------- Contract & BOQ
        boq, sections = [], []
        if order:
            current = None
            for line in order.order_line:
                if line.display_type == "line_section":
                    current = {"name": line.name, "amount": 0.0, "certified": 0.0}
                    sections.append(current)
                    continue
                if line.display_type or line.is_downpayment:
                    continue
                certified_amount = line.qty_delivered * line.price_unit
                row = {
                    "rec": line, "code": line.product_id.default_code or "", "name": line.product_id.name or line.name,
                    "uom": line.product_uom_id.name if "product_uom_id" in line._fields else "",
                    "qty": line.product_uom_qty, "price": line.price_unit, "amount": line.price_subtotal,
                    "qty_done": line.qty_delivered, "qty_invoiced": line.qty_invoiced,
                    "pct": (line.qty_delivered / line.product_uom_qty * 100.0) if line.product_uom_qty else 0.0,
                    "amount_done": certified_amount, "section": current["name"] if current else "",
                }
                boq.append(row)
                if current:
                    current["amount"] += line.price_subtotal
                    current["certified"] += certified_amount
        data["boq"] = boq
        data["boq_sections"] = sections

        # ---------------- Variation orders
        vos = env["hom.variation.order"].search([("sale_order_id", "=", order.id)], order="date, id") if order else []
        data["variations"] = [{"rec": v, "name": v.name, "title": v.title, "date": v.date, "reason": sel(v, "reason"),
                               "ref": v.reference or "", "amount": v.amount_total, "cost": v.cost_total,
                               "state": sel(v, "state")} for v in vos]

        # ---------------- Owner certificates
        certs = env["hom.progress.certificate"].search([("sale_order_id", "=", order.id)], order="date, id") if order else []
        data["certificates"] = [{
            "rec": c, "name": c.name, "date": c.date, "period_end": c.period_end, "state": sel(c, "state"),
            "current": c.amount_current, "cumulative": c.amount_cumulative, "advance": c.amount_advance_recovery,
            "retention": c.amount_retention, "net": c.amount_net, "progress": c.progress_pct,
            "invoice": c.invoice_id, "payment": sel(c.invoice_id, "payment_state") if c.invoice_id else "",
        } for c in certs if in_period(c.date)]

        # ---------------- Customer invoices and payments (posted accounting)
        invoices = order.invoice_ids.filtered(lambda m: m.state != "cancel").sorted(lambda m: (m.invoice_date or m.date, m.id)) \
            if order else env["account.move"]
        retention_product = company.hom_retention_owner_product_id
        inv_rows, payments = [], {}
        totals = defaultdict(float)
        for move in invoices:
            sign = -1 if move.move_type == "out_refund" else 1
            lines = move.invoice_line_ids.filtered(lambda l: l.display_type == "product")
            work = sum(lines.filtered(lambda l: not l.is_downpayment and l.product_id != retention_product).mapped("price_subtotal"))
            dp = sum(lines.filtered("is_downpayment").mapped("price_subtotal"))
            ret = sum(lines.filtered(lambda l: l.product_id == retention_product).mapped("price_subtotal"))
            kind = _("Advance") if dp > 0 and not work else (_("Retention release") if ret > 0 and not work else
                                                           (_("Credit note") if sign < 0 else _("Certificate")))
            paid = move.amount_total - move.amount_residual if move.state == "posted" else 0.0
            inv_rows.append({"rec": move, "name": move.name or _("Draft"), "date": move.invoice_date, "kind": kind,
                             "state": sel(move, "state"), "untaxed": sign * move.amount_untaxed, "tax": sign * move.amount_tax,
                             "total": sign * move.amount_total, "paid": sign * paid, "residual": sign * move.amount_residual,
                             "payment_state": sel(move, "payment_state")})
            if move.state == "posted":
                totals["work"] += sign * work
                totals["dp"] += sign * dp
                totals["ret"] += sign * ret
                totals["untaxed"] += sign * move.amount_untaxed
                totals["tax"] += sign * move.amount_tax
                totals["total"] += sign * move.amount_total
                totals["paid"] += sign * paid
                totals["residual"] += sign * move.amount_residual
                receivable = move.line_ids.filtered(lambda l: l.account_id.account_type == "asset_receivable")
                for partial in receivable.matched_credit_ids | receivable.matched_debit_ids:
                    counterpart = (partial.credit_move_id | partial.debit_move_id) - receivable
                    for aml in counterpart:
                        pay = aml.move_id
                        if pay == move or pay.move_type in ("out_invoice", "out_refund"):
                            continue
                        key = pay.id
                        payments.setdefault(key, {"rec": pay, "name": pay.name, "date": pay.date,
                                                  "journal": pay.journal_id.name, "amount": 0.0, "invoices": []})
                        payments[key]["amount"] += partial.amount
                        payments[key]["invoices"].append(move.name)
        data["invoices"] = [r for r in inv_rows if in_period(r["date"])]
        data["payments"] = sorted([p for p in payments.values() if in_period(p["date"])], key=lambda p: (p["date"], p["name"]))
        data["inv_totals"] = totals

        # ---------------- Purchases, subcontracts, vendor bills
        po_lines = env["purchase.order.line"].search(an_domain("purchase.order.line") + [("order_id.state", "in", ("purchase", "done"))])
        orders = po_lines.order_id
        purchases, subcontracts = [], []
        committed = 0.0
        for po in orders.sorted(lambda o: (o.date_approve or o.date_order, o.id)):
            lines = po_lines.filtered(lambda l: l.order_id == po)
            amount = sum(lines.mapped("price_subtotal"))
            received = sum(l.qty_received * l.price_unit for l in lines)
            billed = sum(l.qty_invoiced * l.price_unit for l in lines)
            committed += max(amount - billed, 0.0)
            row = {"rec": po, "name": po.name, "partner": po.partner_id.name, "date": (po.date_approve or po.date_order).date(),
                   "amount": amount, "received": received, "billed": billed, "open": max(amount - billed, 0.0),
                   "buyer": po.user_id.name or ""}
            if po.hom_is_subcontract:
                row.update({
                    "advance": po.hom_advance_billed, "recovered": po.hom_advance_recovered,
                    "advance_left": po.hom_advance_remaining, "retention_pct": po.hom_retention_pct,
                    "certificates": [{"rec": c, "name": c.name, "date": c.date, "state": sel(c, "state"),
                                      "current": c.amount_current, "advance": c.amount_advance_recovery,
                                      "retention": c.amount_retention, "net": c.amount_net, "bill": c.bill_id,
                                      "b2b": c.back_to_back_warning}
                                     for c in po.hom_sub_certificate_ids.sorted("date") if in_period(c.date)],
                })
                row["retention_held"] = -sum(c["retention"] for c in row["certificates"] if c["bill"])
                subcontracts.append(row)
            else:
                purchases.append(row)
        data["purchases"] = purchases
        data["subcontracts"] = subcontracts
        data["committed"] = committed

        # ---------------- Guarantees
        guarantees = env["hom.bank.guarantee"].search(["|", ("sale_order_id", "=", order.id if order else 0),
                                                       ("purchase_order_id", "in", orders.ids)], order="issue_date, id")
        data["guarantees"] = [{"rec": g, "name": g.name, "direction": sel(g, "direction"), "type": sel(g, "guarantee_type"),
                               "partner": g.partner_id.name, "bank": g.bank_name or "", "amount": g.amount,
                               "current": g.amount_current, "issue": g.issue_date, "expiry": g.expiry_date,
                               "state": sel(g, "state")} for g in guarantees]

        bill_lines = env["account.move.line"].search(an_domain("account.move.line") + [
            ("move_id.move_type", "in", ("in_invoice", "in_refund")), ("parent_state", "=", "posted")])
        bills = []
        for move in bill_lines.move_id.sorted(lambda m: (m.invoice_date or m.date, m.id)):
            mine = bill_lines.filtered(lambda l: l.move_id == move)
            sign = -1 if move.move_type == "in_refund" else 1
            if not in_period(move.invoice_date or move.date):
                continue
            bills.append({"rec": move, "name": move.name, "ref": move.ref or "", "partner": move.partner_id.name,
                          "date": move.invoice_date, "project_amount": sign * sum(mine.mapped("price_subtotal")),
                          "total": sign * move.amount_total, "residual": sign * move.amount_residual,
                          "payment_state": sel(move, "payment_state"),
                          "origin": move.invoice_origin or ""})
        data["bills"] = bills

        # ---------------- Materials movements
        moves = env["stock.move"]
        if "project_id" in env["stock.picking"]._fields:
            moves |= env["stock.move"].search([("picking_id.project_id", "=", project.id), ("state", "=", "done")])
        moves |= env["stock.move"].search([("purchase_line_id", "in", po_lines.ids), ("state", "=", "done")])
        data["materials"] = [{
            "rec": m, "date": m.date, "ref": m.reference or m.picking_id.name or "", "picking": m.picking_id,
            "product": m.product_id.display_name, "qty": m.quantity if "quantity" in m._fields else m.product_uom_qty,
            "uom": (m.uom_id if "uom_id" in m._fields else m.product_uom).name, "src": m.location_id.display_name, "dest": m.location_dest_id.display_name,
            "value": abs(m.value) if "value" in m._fields else 0.0,
            "kind": _("Issue to site") if m.picking_id.project_id == project and m.location_id.usage == "internal"
            and m.location_dest_id.usage != "internal" else (_("Receipt") if m.location_id.usage == "supplier" else _("Transfer")),
        } for m in moves.sorted(lambda m: (m.date, m.id)) if in_period(m.date.date())]

        # ---------------- Expenses / custody
        expenses = []
        if "hr.expense" in env:
            for e in env["hr.expense"].search(an_domain("hr.expense"), order="date, id"):
                if in_period(e.date):
                    expenses.append({"rec": e, "date": e.date, "employee": e.employee_id.name, "name": e.name,
                                     "amount": e.total_amount if "total_amount" in e._fields else e.total_amount_currency,
                                     "state": sel(e, "state"), "move": e.account_move_id if "account_move_id" in e._fields else False})
        data["expenses"] = expenses

        # ---------------- Analytic ledger: every money movement booked on the project
        ledger, by_account, by_trade = [], defaultdict(lambda: [0.0, 0]), defaultdict(lambda: [0.0, 0.0])
        trade_plans = env["account.analytic.plan"]
        labour = defaultdict(lambda: [0.0, 0.0])
        revenue = cost = 0.0
        if account:
            col = account.plan_id._column_name()
            trade_plans = env["account.analytic.plan"].search([("parent_id", "=", False), ("id", "!=", account.root_plan_id.id)])
            trade_cols = [p._column_name() for p in trade_plans if p._column_name() in env["account.analytic.line"]._fields]
            dom = [(col, "=", account.id)]
            if date_from:
                dom.append(("date", ">=", date_from))
            if date_to:
                dom.append(("date", "<=", date_to))
            for al in env["account.analytic.line"].search(dom, order="date, id"):
                trade = next((al[c] for c in trade_cols if al[c]), env["account.analytic.account"])
                move = al.move_line_id.move_id
                ledger.append({"rec": al, "date": al.date, "move": move, "ref": move.name or al.ref or "",
                               "account": al.general_account_id.display_name if al.general_account_id else _("Timesheet"),
                               "partner": al.partner_id.name or (al.employee_id.name if "employee_id" in al._fields and al.employee_id else ""),
                               "label": al.name or "", "trade": trade.name or "", "amount": al.amount})
                key = al.general_account_id.display_name if al.general_account_id else _("Timesheets (labour)")
                by_account[key][0] += al.amount
                by_account[key][1] += 1
                if al.amount >= 0:
                    revenue += al.amount
                else:
                    cost -= al.amount
                    by_trade[trade.name or _("Unassigned")][1] -= al.amount
                if "employee_id" in al._fields and al.employee_id and not al.move_line_id:
                    labour[al.employee_id.name][0] += al.unit_amount
                    labour[al.employee_id.name][1] -= al.amount
        data["ledger"] = ledger
        data["ledger_by_account"] = sorted(by_account.items(), key=lambda kv: -abs(kv[1][0]))
        data["labour"] = sorted(labour.items())
        data["revenue_booked"] = revenue
        data["cost_booked"] = cost

        # ---------------- Budget vs actual by trade
        budget = []
        if "budget.line" in env and account:
            for bl in env["budget.line"].search([("account_id", "=", account.id)]):
                trade = next((bl[c] for c in bl._fields if c.startswith("x_plan") and bl[c]), env["account.analytic.account"])
                budget.append({"rec": bl, "budget": bl.budget_analytic_id.name, "trade": trade.name or "",
                               "amount": abs(bl.budget_amount), "committed": abs(getattr(bl, "committed_amount", 0.0) or 0.0),
                               "achieved": abs(bl.achieved_amount),
                               "pct": (abs(bl.achieved_amount) / abs(bl.budget_amount) * 100.0) if bl.budget_amount else 0.0})
        data["budget"] = budget
        data["budget_total"] = sum(b["amount"] for b in budget)

        # ---------------- Site: measurements, daily reports, submittals
        meas = env["hom.site.measurement"].search([("sale_line_id.order_id", "=", order.id)], order="date, id") if order else []
        data["measurements"] = [{"rec": m, "date": m.date, "code": m.product_id.default_code or "", "item": m.product_id.name,
                                 "qty": m.quantity, "uom": m.product_uom_id.name, "location": m.location or "",
                                 "engineer": m.user_id.name or "", "certificate": m.certificate_id, "state": sel(m, "state")}
                                for m in meas if in_period(m.date)]
        daily = []
        if "x_site_daily_report" in env:
            D = env["x_site_daily_report"]
            for r in D.search([("x_studio_project", "=", project.id)], order="x_studio_date, id"):
                if in_period(r.x_studio_date):
                    daily.append({"rec": r, "date": r.x_studio_date, "status": sel(r, "x_studio_status"),
                                  "manpower": r.x_studio_manpower, "progress": r.x_studio_progress_pct,
                                  "reason": sel(r, "x_studio_block_reason") if r.x_studio_status != "on_track" else "",
                                  "engineer": r.x_studio_site_engineer.name or ""})
        data["daily"] = daily
        subs = env["hom.submittal"].search([("project_id", "=", project.id)], order="name") if "hom.submittal" in env else []
        data["submittals"] = [{"rec": s, "name": s.name, "title": s.title, "type": sel(s, "submittal_type"),
                               "rev": s.revision, "submitted": s.submitted_date, "due": s.due_date,
                               "code": s.review_code or "", "state": sel(s, "state"), "overdue": s.is_overdue}
                              for s in subs]

        # ---------------- Schedule
        data["milestones"] = [{"rec": m, "name": m.name, "deadline": m.deadline, "reached": m.is_reached,
                               "reached_date": m.reached_date if "reached_date" in m._fields else False}
                              for m in project.milestone_ids.sorted(lambda m: (m.deadline or fields.Date.today(), m.id))]
        data["tasks"] = [{"rec": t, "name": t.name, "stage": t.stage_id.name or "", "users": ", ".join(t.user_ids.mapped("name")),
                          "deadline": t.date_deadline, "done": t.is_closed if "is_closed" in t._fields else False}
                         for t in project.task_ids.sorted(lambda t: (t.stage_id.sequence, t.id))]
        data["updates"] = [{"rec": u, "date": u.date, "name": u.name, "status": sel(u, "status"), "progress": u.progress}
                           for u in project.update_ids.sorted("date", reverse=True)[:6]]

        # ---------------- Accounting tie-out: dossier figures vs general ledger
        tie = []
        if order:
            AML = env["account.move.line"]
            posted = invoices.filtered(lambda m: m.state == "posted")
            receivable_gl = sum(AML.search([("move_id", "in", posted.ids), ("account_id.account_type", "=", "asset_receivable"),
                                            ("reconciled", "=", False)]).mapped("amount_residual"))
            tie.append((_("Owner balance (receivable)"), totals["residual"], receivable_gl))
            dp_lines = AML.search([("move_id", "in", posted.ids), ("is_downpayment", "=", True), ("display_type", "=", "product")])
            tie.append((_("Advance still to recover"), order.hom_advance_outstanding, -sum(dp_lines.mapped("balance"))))
            ret_lines = AML.search([("move_id", "in", (posted | order.hom_retention_release_ids.filtered(lambda m: m.state == "posted")).ids),
                                    ("product_id", "=", retention_product.id)]) if retention_product else AML
            tie.append((_("Retention to collect"), order.hom_retention_balance, sum(ret_lines.mapped("balance"))))
            work_lines = AML.search([("move_id", "in", posted.ids), ("display_type", "=", "product"), ("is_downpayment", "=", False),
                                     ("product_id", "!=", retention_product.id or 0)])
            tie.append((_("Work invoiced (revenue)"), totals["work"], -sum(work_lines.mapped("balance"))))
        data["tie_out"] = [{"label": l, "system": s, "gl": g, "ok": cur.is_zero(s - g)} for l, s, g in tie]

        # ---------------- Executive figures
        contract = order.amount_untaxed if order else 0.0
        etc = project.hom_estimate_to_complete or 0.0
        eac = cost + etc
        data["kpi"] = {
            "contract": contract,
            "variations": sum(v["amount"] for v in data["variations"] if v["rec"].state in ("approved", "applied")),
            "certified": order.hom_certified_amount if order else 0.0,
            "certified_pct": order.hom_certified_pct if order else 0.0,
            "invoiced_work": totals["work"], "invoiced_total": totals["total"], "collected": totals["paid"],
            "receivable": totals["residual"],
            "advance_received": order.hom_advance_received if order else 0.0,
            "advance_left": order.hom_advance_outstanding if order else 0.0,
            "retention": order.hom_retention_balance if order else 0.0,
            "revenue": revenue, "cost": cost, "committed": committed, "budget": data["budget_total"],
            "etc": etc, "eac": eac,
            "margin_now": ((revenue - cost) / revenue * 100.0) if revenue else 0.0,
            "margin_eac": ((contract - eac) / contract * 100.0) if contract and etc else None,
            "sub_payable": sum(b["residual"] for b in bills),
            "guarantees_out": sum(g["current"] for g in data["guarantees"] if g["rec"].direction == "issued"
                                  and g["rec"].state not in ("released", "cancel")),
        }
        data["counts"] = {k: len(data[k]) for k in ("certificates", "invoices", "payments", "purchases", "subcontracts",
                                                    "bills", "materials", "expenses", "ledger", "measurements",
                                                    "submittals", "variations", "guarantees")}
        return data


class ReportProjectDossier(models.AbstractModel):
    _name = "report.hom_project_dossier.report_project_dossier"
    _description = "H.O.M Project Dossier"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        projects = self.env["project.project"].browse(docids or data.get("project_ids") or [])
        date_from = fields.Date.to_date(data.get("date_from")) if data.get("date_from") else False
        date_to = fields.Date.to_date(data.get("date_to")) if data.get("date_to") else False
        cur = self.env.company.currency_id

        def link(rec, label):
            label = "" if label in (None, False) else str(label)
            if not rec:
                return label
            href = "%s/odoo/%s/%s" % (rec.get_base_url().rstrip("/"), rec._name, rec.id)
            return Markup('<a href="%s" target="_top" class="lnk" style="unicode-bidi: embed">%s</a>') % (href, label)

        def fmt(value, digits=2):
            if value is None:
                return "-"
            return ("{:,.%sf}" % digits).format(value or 0.0)

        return {
            "doc_ids": projects.ids, "doc_model": "project.project", "docs": projects,
            "dossiers": {p.id: p._hom_dossier(date_from, date_to) for p in projects},
            "fmt": fmt, "link": link, "currency": cur, "printed_on": fields.Datetime.context_timestamp(self, fields.Datetime.now()),
            "printed_by": self.env.user.name,
        }
