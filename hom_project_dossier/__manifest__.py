{
    "name": "Ziij Construction - Project Dossier",
    "summary": "One printable, clickable report per project (ملف المشروع الشامل): contract, BOQ progress, "
               "certificates, invoices, collections, guarantees, purchases, subcontracts, materials, expenses, "
               "labour, budget, site records and every accounting movement, reconciled to the general ledger.",
    "version": "20.0.1.0.5",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["hom_progress_billing", "hom_subcontract_billing", "hom_variation_order", "hom_guarantees",
                "hom_submittals", "purchase_stock", "project_stock", "hr_expense", "hr_timesheet"],
    "data": [
        "security/ir.access.csv",
        "report/project_dossier_report.xml",
        "views/dossier_views.xml",
    ],
    "installable": True,
}
