{
    "name": "Ziij Construction - Commercial & Close-out Plus",
    "summary": "Cash-flow forecast per contract (owner collections versus supplier and subcontractor payments, peak "
               "funding), subcontractor packages with offers compared against the item budget and awarded, "
               "subcontractor claims against us passed back-to-back to the owner, the handover documents checklist "
               "(as-built, O&M manuals, warranties, tests) and site safety on the daily report.",
    "version": "20.0.1.2.2",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["zc_site_records", "zc_material_request", "zc_boq_item_card", "zc_tender"],
    "data": [
        "security/ir.access.csv",
        "data/data.xml",
        "views/cashflow_views.xml",
        "views/sub_package_views.xml",
        "views/claim_handover_safety_views.xml",
        "views/automation_views.xml",
    ],
    "installable": True,
}
