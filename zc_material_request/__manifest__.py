{
    "name": "Ziij Construction - Material Requests",
    "summary": "Purchasing starts from a documented site need (طلب مواد): the site engineer asks for a material, a "
               "quantity, a date and a BOQ item; the project manager approves; the request is served from the site "
               "store, transferred from another store, or turned into a request for quotation checked against the "
               "item budget. Receipts are accepted or rejected technically before the material can be used.",
    "version": "20.0.1.0.1",
    "category": "Services/Project",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["zc_certificate_controls"],
    "data": [
        "security/ir.access.csv",
        "data/data.xml",
        "views/material_request_views.xml",
    ],
    "installable": True,
}
