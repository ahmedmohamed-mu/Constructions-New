{
    "name": "H.O.M Contracting - Base",
    "summary": "Contract terms on sales/purchase orders and contracting accounts (advance, retention).",
    "version": "20.0.1.2.0",
    "category": "Services/Contracting",
    "author": "Ziij AI",
    "license": "OPL-1",
    "depends": ["sale", "purchase", "account"],
    "data": [
        "data/product_data.xml",
        "views/res_config_settings_views.xml",
        "views/sale_order_views.xml",
        "views/purchase_order_views.xml",
        "views/menu_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
}
