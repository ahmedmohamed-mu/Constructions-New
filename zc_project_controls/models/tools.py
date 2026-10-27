from calendar import monthrange
from datetime import date, timedelta


def project_order(project):
    return project.reinvoiced_sale_order_id or project.sale_order_id


def month_start(d):
    return date(d.year, d.month, 1)


def month_end(d):
    return date(d.year, d.month, monthrange(d.year, d.month)[1])


def next_month(d):
    return month_end(d) + timedelta(days=1)


def months_between(start, end):
    out, m = [], month_start(start)
    while m <= end:
        out.append(m)
        m = next_month(m)
    return out


def analytic_amount(env, account, date_to=None, date_from=None, sign=None, exclude_general_accounts=()):
    """Sum of analytic lines of ``account`` (in its own plan column). sign='cost' -> costs as a positive number,
    sign='revenue' -> revenues; None -> raw balance."""
    if not account:
        return 0.0
    column = account.plan_id._column_name()
    domain = [(column, "=", account.id)]
    if date_to:
        domain.append(("date", "<=", date_to))
    if date_from:
        domain.append(("date", ">=", date_from))
    if sign == "cost":
        domain.append(("amount", "<", 0))
    elif sign == "revenue":
        domain.append(("amount", ">", 0))
    if exclude_general_accounts:
        domain.append(("general_account_id", "not in", list(exclude_general_accounts)))
    lines = env["account.analytic.line"].sudo().search(domain)
    total = sum(lines.mapped("amount"))
    return -total if sign == "cost" else total
