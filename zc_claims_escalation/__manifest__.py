{
    "name": "Ziij Construction - Claims & Price Escalation",
    "summary": "Contractor claims (extension of time and cost) with the contractual notice periods and time-bar "
               "alerts (notice, detailed claim, engineer's answer, notice of dissatisfaction), and price "
               "escalation (فروق الأسعار) by weighted price indices against the base date of the contract.",
    "version": "20.0.1.0.0",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["zc_contract_closeout"],
    "data": [
        "security/ir.access.csv",
        "data/data.xml",
        "views/claim_views.xml",
        "views/escalation_views.xml",
        "views/sale_order_views.xml",
        "views/menus.xml",
    ],
    "post_init_hook": "_zc_ce_post_init",
    "installable": True,
}
