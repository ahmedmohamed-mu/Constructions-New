from odoo.addons.zc_project_controls.hooks import _account

ACCOUNTS = {
    "wht_rec": ("102022", "asset_current", "Withholding tax deducted by customers", "خصم تحت حساب الضريبة - عملاء"),
    "si_rec": ("102023", "asset_current", "Social insurance deducted by owners",
               "تأمينات اجتماعية مخصومة من المستخلصات - تحت التسوية"),
    "wht_pay": ("201032", "liability_current", "Withholding tax payable - subcontractors",
                "ضريبة خصم من المنبع - مستحقة على مقاولي الباطن"),
    "owner_ded": ("400018", "expense_direct_cost", "Owner deductions on certificates (penalties, utilities)",
                  "خصومات المالك على المستخلصات (غرامات واستهلاكات)"),
    "backcharge": ("400017", "expense_direct_cost", "Back-charges recovered from subcontractors",
                   "خصومات مستردة من مقاولي الباطن (خامات ومخالفات)"),
}

TYPES = [
    # code, scope, timing, method, pct, account key, analytic, english, arabic, sequence
    ("WHT", "owner", "payment", "percent", 1.0, "wht_rec", False,
     "Withholding tax (deducted by the owner)", "خصم تحت حساب الضريبة (يخصمه المالك)", 10),
    ("SI", "owner", "payment", "percent", 5.0, "si_rec", False,
     "Social insurance (government owners)", "تأمينات اجتماعية (جهات حكومية)", 20),
    ("PEN", "owner", "invoice", "fixed", 0.0, "owner_ded", True,
     "Delay penalty", "غرامة تأخير", 30),
    ("UTL", "owner", "invoice", "fixed", 0.0, "owner_ded", True,
     "Water and electricity consumption", "استهلاك مياه وكهرباء", 40),
    ("MAT", "subcontract", "invoice", "fixed", 0.0, "backcharge", True,
     "Materials issued from our stores", "خامات منصرفة من مخازن الشركة", 10),
    ("BCK", "subcontract", "invoice", "fixed", 0.0, "backcharge", True,
     "Back-charge / site violations", "خصم مخالفات وأعمال على حساب المقاول", 20),
    ("SPEN", "subcontract", "invoice", "fixed", 0.0, "backcharge", True,
     "Delay penalty", "غرامة تأخير", 30),
    ("SWHT", "subcontract", "payment", "percent", 1.0, "wht_pay", False,
     "Withholding tax (we deduct)", "خصم تحت حساب الضريبة (نخصمه من المقاول)", 40),
]


def _zc_cc_post_init(env):
    env = env(su=True)
    arabic = bool(env["res.lang"].search_count([("code", "=", "ar_001")]))
    for company in env["res.company"].search([]):
        if not company.chart_template or not env["account.account"].with_company(company).search_count([], limit=1):
            continue
        accounts = {key: _account(env, company, *spec) for key, spec in ACCOUNTS.items()}
        Type = env["zc.deduction.type"]
        for code, scope, timing, method, pct, acc, analytic, en, ar, seq in TYPES:
            if Type.search_count([("company_id", "=", company.id), ("code", "=", code)]):
                continue
            rec = Type.create({"name": en, "code": code, "scope": scope, "timing": timing, "method": method,
                               "default_pct": pct, "account_id": accounts[acc].id, "analytic": analytic,
                               "company_id": company.id, "sequence": seq})
            if arabic:
                rec.with_context(lang="ar_001").name = ar
        if not company.zc_settlement_journal_id:
            company.zc_settlement_journal_id = env["account.journal"].search(
                [("type", "=", "general"), ("company_id", "=", company.id)], limit=1)
