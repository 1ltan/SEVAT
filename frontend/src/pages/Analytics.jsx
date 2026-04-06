import React, { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import {
    BarChart, Bar, PieChart, Pie, Cell, LineChart, Line,
    XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend
} from "recharts";
import { getSummary, generateReport, generatePdfReport, getCameras } from "../api/client";
import { useLanguage } from "../context/LanguageContext";

const PIE_COLORS = [
    "#ff1a1a", 
    "#ff6600", 
    "#555555",
    "#00aaff",
    "#ffcc00", 
    "#808080", 
    "#cc0000", 
];

const CustomTooltip = ({ active, payload, label }) => {
    if (!active || !payload?.length) return null;
    return (
        <div className="custom-tooltip">
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
            {payload.map(p => (
                <div key={p.name} style={{ color: p.color }}>
                    {p.name}: <strong>{p.value}</strong>
                </div>
            ))}
        </div>
    );
};

export default function Analytics() {
    const { t } = useLanguage();
    const [reportParams, setReportParams] = useState({
        date_from: new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10),
        date_to: new Date().toISOString().slice(0, 10),
        camera_ids: [],
        class_names: [],
        group_by: "day",
    });
    const [reportData, setReportData] = useState([]);
    const [showPrint, setShowPrint] = useState(false);
    const [pdfLoading, setPdfLoading] = useState(false);
    const [pdfError, setPdfError] = useState("");

    const { data: sumData, isLoading: sumLoading } = useQuery({
        queryKey: ["analytics-summary"],
        queryFn: getSummary,
    });
    const { data: camData } = useQuery({ queryKey: ["cameras"], queryFn: getCameras });
    const cameras = camData?.data || [];
    const summary = sumData?.data || {};
    const classBreakdown = summary.class_breakdown || [];

    const CLASS_OPTIONS = [
        { value: "APC",   label: t.classLabels.APC },
        { value: "IFV",   label: t.classLabels.IFV },
        { value: "TANK",  label: t.classLabels.TANK },
        { value: "CAR",   label: t.classLabels.CAR },
        { value: "TRUCK", label: t.classLabels.TRUCK },
        { value: "ART",   label: t.classLabels.ART },
        { value: "MLRS",  label: t.classLabels.MLRS },
    ];

    const reportMut = useMutation({
        mutationFn: generateReport,
        onSuccess: (res) => {
            setReportData(res?.data || []);
            setShowPrint(true);
        },
    });

    const buildParams = () => ({
        ...reportParams,
        date_from: new Date(reportParams.date_from).toISOString(),
        date_to: new Date(reportParams.date_to + "T23:59:59").toISOString(),
    });

    const handleDownloadPdf = async () => {
        setPdfError("");
        setPdfLoading(true);
        try {
            await generatePdfReport({ ...buildParams(), language: "en" });
        } catch (e) {
            let msg = e.message || t.analytics.errDefault;
            if (msg.includes("500") || msg.includes("Internal")) {
                msg = t.analytics.errServer;
            } else if (msg.includes("timeout") || msg.includes("Timeout")) {
                msg = t.analytics.errTimeout;
            }
            setPdfError(msg);
        } finally {
            setPdfLoading(false);
        }
    };

    const toggleArr = (arr, val) =>
        arr.includes(val) ? arr.filter(v => v !== val) : [...arr, val];

    return (
        <div>
            <div className="page-header">
                <div><div className="page-title">{t.analytics.pageTitle}</div></div>
            </div>

            {/* Summary stats */}
            {!sumLoading && (
                <div className="stats-grid" style={{ marginBottom: 24 }}>
                    <div className="stat-card">
                        <div className="stat-value">{summary.total_confirmed ?? "—"}</div>
                        <div className="stat-label">{t.analytics.totalConfirmed}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-value" style={{ color: "var(--success)" }}>{summary.auto_confirmed ?? "—"}</div>
                        <div className="stat-label">{t.analytics.autoConfirmed}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-value" style={{ color: "var(--info)" }}>{summary.operator_confirmed ?? "—"}</div>
                        <div className="stat-label">{t.analytics.byOperator}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-value" style={{ color: "var(--text-secondary)" }}>{summary.rejected ?? "—"}</div>
                        <div className="stat-label">{t.analytics.rejected}</div>
                    </div>
                    <div className="stat-card">
                        <div className="stat-value" style={{ color: "var(--warning)" }}>{summary.pending ?? "—"}</div>
                        <div className="stat-label">{t.analytics.pending}</div>
                    </div>
                </div>
            )}

            {/* Class distribution pie */}
            {classBreakdown.length > 0 && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
                    <div className="card">
                        <div style={{ fontWeight: 600, marginBottom: 16 }}>{t.analytics.classDistribution}</div>
                        <ResponsiveContainer width="100%" height={220}>
                            <PieChart>
                                <Pie data={classBreakdown} dataKey="count" nameKey="class_name"
                                    cx="50%" cy="50%" innerRadius={55} outerRadius={80} paddingAngle={3} stroke="none"
                                    label={({ class_name }) => class_name} labelLine={{ stroke: "var(--text-secondary)", opacity: 0.6 }}>
                                    {classBreakdown.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                                </Pie>
                                <Tooltip content={<CustomTooltip />} />
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                    <div className="card">
                        <div style={{ fontWeight: 600, marginBottom: 16 }}>{t.analytics.classCount}</div>
                        <ResponsiveContainer width="100%" height={220}>
                            <BarChart data={classBreakdown}>
                                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                                <XAxis dataKey="class_name" tick={{ fill: "var(--text-secondary)", fontSize: 11 }} />
                                <YAxis tick={{ fill: "var(--text-secondary)", fontSize: 11 }} />
                                <Tooltip content={<CustomTooltip />} />
                                <Bar dataKey="count" fill="var(--accent)" name={t.analytics.countLabel} radius={[4, 4, 0, 0]} />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            )}

            {/* Report builder */}
            <div className="card" style={{ marginBottom: 16 }}>
                <div style={{ fontWeight: 600, marginBottom: 16 }}>{t.analytics.generateReport}</div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto auto auto", gap: 12, alignItems: "end", flexWrap: "wrap" }}>
                    <div className="form-group">
                        <label className="form-label">{t.analytics.from}</label>
                        <input type="date" className="input"
                            value={reportParams.date_from}
                            onChange={e => setReportParams({ ...reportParams, date_from: e.target.value })} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">{t.analytics.to}</label>
                        <input type="date" className="input"
                            value={reportParams.date_to}
                            onChange={e => setReportParams({ ...reportParams, date_to: e.target.value })} />
                    </div>
                    <div className="form-group">
                        <label className="form-label">{t.analytics.groupBy}</label>
                        <select className="select"
                            value={reportParams.group_by}
                            onChange={e => setReportParams({ ...reportParams, group_by: e.target.value })}>
                            <option value="day">{t.analytics.byDay}</option>
                            <option value="camera">{t.analytics.byCamera}</option>
                            <option value="class">{t.analytics.byClass}</option>
                        </select>
                    </div>
                    <button className="btn btn-primary"
                        disabled={reportMut.isPending}
                        onClick={() => reportMut.mutate(buildParams())}>
                        {reportMut.isPending ? t.analytics.generating : t.analytics.generate}
                    </button>
                    <button
                        className="btn btn-primary"
                        disabled={pdfLoading}
                        onClick={handleDownloadPdf}
                        title={t.analytics.pdfTitle}
                        style={{ background: "transparent", border: "1px solid var(--accent)", color: "var(--accent)" }}
                    >
                        {pdfLoading ? t.analytics.pdfBtnLoading : t.analytics.pdfBtn}
                    </button>
                </div>
                {pdfError && (
                    <div style={{ color: "var(--danger, #ff4444)", fontSize: 12, marginTop: 8 }}>
                         {pdfError}
                    </div>
                )}
                <div style={{ marginTop: 12, display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <div style={{ fontSize: 12, color: "var(--text-secondary)", marginRight: 4 }}>{t.analytics.cameras}</div>
                    {cameras.map(c => (
                        <button key={c.id}
                            className={`btn btn-ghost btn-sm ${reportParams.camera_ids.includes(c.id) ? "active" : ""}`}
                            style={reportParams.camera_ids.includes(c.id) ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}}
                            onClick={() => setReportParams({ ...reportParams, camera_ids: toggleArr(reportParams.camera_ids, c.id) })}>
                            {c.name}
                        </button>
                    ))}
                </div>
                <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
                    <div style={{ fontSize: 12, color: "var(--text-secondary)", marginRight: 4 }}>{t.analytics.classes}</div>
                    {CLASS_OPTIONS.map(o => (
                        <button key={o.value}
                            className={`btn btn-ghost btn-sm ${reportParams.class_names.includes(o.value) ? "active" : ""}`}
                            style={reportParams.class_names.includes(o.value) ? { borderColor: "var(--accent)", color: "var(--accent)" } : {}}
                            onClick={() => setReportParams({ ...reportParams, class_names: toggleArr(reportParams.class_names, o.value) })}>
                            {o.label}
                        </button>
                    ))}
                </div>
            </div>

            {/* Report chart */}
            {showPrint && reportData.length > 0 && (
                <div className="card">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                        <div style={{ fontWeight: 600 }}>{t.analytics.reportResult(reportData.length)}</div>
                        <div style={{ display: "flex", gap: 8 }}>
                            <button
                                className="btn btn-primary"
                                disabled={pdfLoading}
                                onClick={handleDownloadPdf}
                                title={t.analytics.pdfTitle}
                                style={{ background: "transparent", border: "1px solid var(--accent)", color: "var(--accent)", fontSize: 13 }}
                            >
                                {pdfLoading ? t.analytics.generatingPdf : t.analytics.downloadPdf}
                            </button>
                            <button className="btn btn-ghost btn-sm" onClick={() => window.print()}>{t.analytics.print}</button>
                        </div>
                    </div>
                    <ResponsiveContainer width="100%" height={260}>
                        <LineChart data={reportData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                            <XAxis dataKey="label" tick={{ fill: "var(--text-secondary)", fontSize: 11 }} />
                            <YAxis tick={{ fill: "var(--text-secondary)", fontSize: 11 }} />
                            <Tooltip content={<CustomTooltip />} />
                            <Legend />
                            <Line type="monotone" dataKey="count" stroke="var(--accent)" strokeWidth={2}
                                dot={{ fill: "var(--accent)", r: 4 }} name={t.analytics.countLabel} />
                        </LineChart>
                    </ResponsiveContainer>
                    <div style={{ marginTop: 12 }}>
                        <div className="table-wrapper">
                            <table>
                                <thead><tr><th>{t.analytics.period}</th><th>{t.analytics.count}</th></tr></thead>
                                <tbody>
                                    {reportData.map((r, i) => (
                                        <tr key={i}><td>{r.label}</td><td style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>{r.count}</td></tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
