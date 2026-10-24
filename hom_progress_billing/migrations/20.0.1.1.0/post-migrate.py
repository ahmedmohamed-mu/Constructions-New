from odoo import SUPERUSER_ID, api

from odoo.addons.hom_progress_billing import _copy_studio_etc


def migrate(cr, version):
    _copy_studio_etc(api.Environment(cr, SUPERUSER_ID, {}))
