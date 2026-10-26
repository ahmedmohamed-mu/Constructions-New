import { Component, onWillStart, proxy, useProps } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";
import { user } from "@web/core/user";

const { DateTime } = luxon;

export class HomControlTower extends Component {
    static template = "hom_control_tower.ControlTower";
    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        const params = (this.props.action && this.props.action.params) || {};
        this.state = proxy({ tab: params.tab || "executive", data: null, loading: true, project: 0 });
        this.tabs = [
            { key: "executive", label: _t("Executive"), icon: "speed" },
            { key: "projects", label: _t("Projects"), icon: "business" },
            { key: "finance", label: _t("Finance & Guarantees"), icon: "account_balance" },
            { key: "fm", label: _t("Facility Management"), icon: "build" },
        ];
        this.labels = { preventive: _t("Preventive"), corrective: _t("Corrective") };
        onWillStart(() => this.load());
    }

    get visibleTabs() {
        // the facility-management tab only for companies that run maintenance contracts
        const d = this.state.data;
        return this.tabs.filter((t) => t.key !== "fm" || !d || d.has_fm);
    }

    async load() {
        this.state.loading = true;
        this.state.data = await this.orm.call("hom.control.tower", "get_dashboard_data", []);
        this.state.loading = false;
    }

    // ------------------------------------------------------------------ formatting
    get lang() {
        return (user.lang || "en_US").replace("_", "-");
    }
    get isRtl() {
        return (user.lang || "").startsWith("ar");
    }
    num(v, digits = 0) {
        return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(v || 0);
    }
    short(v) {
        const a = Math.abs(v || 0);
        if (a >= 1e6) {
            return _t("%s M", this.num(v / 1e6, 1));
        }
        if (a >= 1e3) {
            return _t("%s K", this.num(v / 1e3, 0));
        }
        return this.num(v, 0);
    }
    get cur() {
        return this.state.data ? this.state.data.currency.symbol : "";
    }
    pct(v) {
        return v === null || v === undefined ? "—" : `${this.num(v, 1)}%`;
    }
    clamp(v) {
        return Math.max(0, Math.min(100, v || 0));
    }
    month(m) {
        return DateTime.fromISO(`${m}-01`).setLocale(this.isRtl ? "ar-EG-u-nu-latn" : "en").toFormat("LLL yy");
    }
    date(d) {
        return d ? DateTime.fromISO(d).setLocale(this.isRtl ? "ar-EG-u-nu-latn" : "en").toFormat("d LLL yyyy") : "";
    }
    statusLabel(s) {
        return { on_track: _t("On track"), at_risk: _t("At risk"), off_track: _t("Off track") }[s] || s;
    }

    // ------------------------------------------------------------------ chart
    get chart() {
        const series = (this.state.data && this.state.data.executive.series) || [];
        const W = 900, H = 240, padL = 52, padB = 28, padT = 12;
        const max = Math.max(1, ...series.flatMap((s) => [s.certified, s.invoiced, s.collected]));
        const n = Math.max(series.length, 1);
        const groupW = (W - padL - 8) / n;
        const barW = Math.min(18, (groupW - 12) / 3);
        const keys = [["certified", "ct-c1"], ["invoiced", "ct-c2"], ["collected", "ct-c3"]];
        const bars = [];
        const labels = [];
        series.forEach((s, i) => {
            const x0 = padL + i * groupW + (groupW - barW * 3 - 4) / 2;
            keys.forEach(([k, cls], j) => {
                const h = ((H - padB - padT) * s[k]) / max;
                bars.push({ key: `${i}-${k}`, x: x0 + j * (barW + 2), y: H - padB - h, w: barW, h: Math.max(h, 0), cls,
                    title: `${this.month(s.month)} · ${this.short(s[k])}` });
            });
            labels.push({ key: `l${i}`, x: padL + i * groupW + groupW / 2, y: H - 8, text: this.month(s.month) });
        });
        const grid = [0.25, 0.5, 0.75, 1].map((f) => ({ key: `g${f}`, y: H - padB - (H - padB - padT) * f,
            text: this.short(max * f) }));
        return { W, H, padL, bars, labels, grid, empty: !series.length };
    }

    agingBars(aging) {
        const parts = [
            ["not_due", _t("Not due"), "ct-a0"], ["d30", _t("1-30 days"), "ct-a1"], ["d60", _t("31-60 days"), "ct-a2"],
            ["d90", _t("61-90 days"), "ct-a3"], ["d90p", _t("Over 90 days"), "ct-a4"],
        ];
        const total = aging.total || 1;
        return parts.map(([k, label, cls]) => ({ key: k, label, cls, value: aging[k], width: (aging[k] / total) * 100 }));
    }

    get agingKinds() {
        return [["ar"], ["ap"]];
    }
    maxOf(rows, key) {
        return Math.max(1, ...rows.map((r) => r[key] || 0));
    }
    budgetMax(rows) {
        return Math.max(1, ...rows.map((r) => Math.max(r.budget, r.committed, r.achieved)));
    }

    get currentProject() {
        const list = this.state.data ? this.state.data.projects : [];
        return list[this.state.project] || list[0];
    }

    // ------------------------------------------------------------------ navigation
    setTab(key) {
        this.state.tab = key;
    }
    selectProject(i) {
        this.state.project = i;
    }
    async open(kind, resId = false) {
        const act = await this.orm.call("hom.control.tower", "get_open_action", [kind, resId]);
        if (act) {
            this.action.doAction(act);
        }
    }
    openAlert(alert) {
        this.action.doAction(alert.action);
    }
}

registry.category("actions").add("hom_control_tower", HomControlTower);
