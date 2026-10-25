# H.O.M Contracting add-ons (Odoo 20)

Thin modules on top of standard Odoo. Everything that Studio/standard covers stays in Studio.

| Module | App / persona | What it adds |
|---|---|---|
| hom_contracting_base | Sales + Purchase + Accounting | Contract terms (advance %, retention %, consultant) on SO; subcontract terms on PO; contracting products & settings |
| hom_progress_billing | Sales (QS) → Consultant → Accounting | Owner progress certificate: cumulative BOQ measurement, approval pushes Delivered qty to the SO, invoice with pro-rata advance recovery + retention, "مستخلص جاري" report |
| hom_subcontract_billing | Site / Purchase → Accounting | Subcontract advance bill + subcontractor certificate: approval pushes Received qty to the PO, bill with advance recovery + retention |
| hom_variation_order | Sales → CEO | Variation order with approval; applies to the SO (new section / qty changes) and revises the project budget when Budget is installed |
| hom_guarantees | Accounting / Sales | Letters of guarantee (bid, advance, performance, retention) issued or received; advance LG reduces with recovery; expiry alerts |
| hom_control_tower | Management (CEO, PM, finance) | Control Tower: executive, projects, finance & guarantees, FM dashboards (navy/teal, dark mode, Arabic) with drill-down alerts |

All modules ship an Arabic translation (`i18n/ar.po`) and share one **Contracting** app menu.

### Automations included
- Site measurements (daily) feed the next owner certificate; monthly draft certificate on the configured day.
- Back-to-back check: subcontractor progress vs owner certified progress → alert to PM + management.
- Budget overrun alert when a purchase order is confirmed.
- Retention release invoice / bill from the contract and the subcontract.
- Weekly project update (certified, invoiced work, actual cost, ETC, expected margin).
- Bank guarantee expiry alerts.

## Deploy on Odoo.sh
1. Copy the 6 folders to the root of the project repo, on a **staging** branch (not main).
2. Push → Odoo.sh builds staging on a copy of production → install the modules from Apps.
3. `hom_contracting_base` copies the existing `x_studio_*` values (SO/PO) automatically on install.
4. Accounting > Settings > Contracting: check retention / advance products and their balance-sheet accounts (102020, 201031, 102021), certificates accountant, VO approver.
5. Archive the Studio server actions "احسب المستخلص" / "احسب مستخلص المقاول" and hide the old x_studio fields once validated.

Tests: `odoo-bin --test-tags /hom_contracting_base,/hom_progress_billing,/hom_subcontract_billing,/hom_variation_order,/hom_guarantees` (11 tests; they reproduce the demo figures, e.g. subcontractor certificate 388,936).
