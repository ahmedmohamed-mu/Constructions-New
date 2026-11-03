from . import models


def _zc_mr_post_init(env):
    env["res.company"].search([])._zc_setup_stock_valuation_accounts()
