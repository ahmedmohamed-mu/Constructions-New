{
    "name": "Ziij Construction - Site Records",
    "summary": "The site diary (التقرير اليومي) that feeds the measurements, the labour and equipment hours and the "
               "evidence of delays for claims; requests for information (RFI) to the consultant with answer deadlines "
               "and their time/cost impact; and non-conformance reports (NCR) from inspection to verified close-out, "
               "with back-charges to the responsible subcontractor.",
    "version": "20.0.1.0.0",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["zc_claims_escalation"],
    "data": [
        "security/ir.access.csv",
        "data/data.xml",
        "views/site_records_views.xml",
    ],
    "installable": True,
}
