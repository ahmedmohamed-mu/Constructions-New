{
    "name": "Ziij Construction - Contract Close-out",
    "summary": "Preliminary and final handover minutes with snag lists, the defects liability period, retention "
               "release (owner and subcontractors), return of the performance guarantees and the final account "
               "(الحساب الختامي) of the contract.",
    "version": "20.0.1.0.0",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["zc_certificate_controls", "hom_guarantees"],
    "data": [
        "security/ir.access.csv",
        "data/data.xml",
        "views/handover_views.xml",
        "views/sale_order_views.xml",
        "views/menus.xml",
        "report/final_account.xml",
    ],
    "installable": True,
}
