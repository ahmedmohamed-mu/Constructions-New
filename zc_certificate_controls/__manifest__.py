{
    "name": "Ziij Construction - Certificate Controls",
    "summary": "The consultant gate and the money gate of the certificate cycle: work inspection requests (WIR) as a "
               "hold point before any quantity is certified, quantities submitted vs certified with the consultant's "
               "cuts tracked as quantity claims, owner and subcontractor deductions (withholding tax, social "
               "insurance, penalties, utilities, back-charges, materials issued to subcontractors), withholdings "
               "settled against the receivable / payable, and the subcontractor approval chain "
               "(site - technical office - project manager).",
    "version": "20.0.1.0.2",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["zc_project_controls", "hom_guarantees", "stock_account"],
    "data": [
        "security/ir.access.csv",
        "data/sequence_data.xml",
        "views/inspection_views.xml",
        "views/dispute_views.xml",
        "views/deduction_views.xml",
        "views/certificate_views.xml",
        "views/subcontract_views.xml",
        "views/stock_views.xml",
        "views/settings_views.xml",
        "views/menus.xml",
        "report/certificate_report.xml",
    ],
    "post_init_hook": "_zc_cc_post_init",
    "installable": True,
}
