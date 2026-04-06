import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getTrash, purgeTrash, restoreFromTrash, getScreenshotUrl } from "../api/client";
import { useLanguage } from "../context/LanguageContext";

// Expiry countdown helper
function getDaysLeft(reviewedAt) {
    if (!reviewedAt) return null;
    const expiry = new Date(reviewedAt).getTime() + 30 * 24 * 60 * 60 * 1000;
    const msLeft = expiry - Date.now();
    return Math.max(0, Math.ceil(msLeft / (24 * 60 * 60 * 1000)));
}

function ExpiryBadge({ reviewedAt, t }) {
    const days = getDaysLeft(reviewedAt);
    if (days === null) return null;

    if (days === 0) {
        return (
            <span style={{
                display: "inline-block",
                padding: "2px 8px",
                borderRadius: 4,
                fontSize: 11,
                fontWeight: 700,
                background: "rgba(239,68,68,0.15)",
                color: "#ef4444",
                border: "1px solid rgba(239,68,68,0.4)",
            }}>
                {t.trash.expiresToday}
            </span>
        );
    }
    if (days <= 7) {
        return (
            <span style={{
                display: "inline-block",
                padding: "2px 8px",
                borderRadius: 4,
                fontSize: 11,
                fontWeight: 700,
                background: "rgba(245,158,11,0.15)",
                color: "#f59e0b",
                border: "1px solid rgba(245,158,11,0.4)",
            }}>
                {t.trash.daysLeft(days)}
            </span>
        );
    }
    return (
        <span style={{
            display: "inline-block",
            padding: "2px 8px",
            borderRadius: 4,
            fontSize: 11,
            fontWeight: 600,
            background: "rgba(107,114,128,0.15)",
            color: "#9ca3af",
            border: "1px solid rgba(107,114,128,0.3)",
        }}>
            {t.trash.daysLeft(days)}
        </span>
    );
}

// Main Trash page
export default function Trash() {
    const { t } = useLanguage();
    const queryClient = useQueryClient();
    const [notification, setNotification] = useState(null);
    const [restoringId, setRestoringId] = useState(null);

    const showNotif = (msg, color = "#22c55e") => {
        setNotification({ msg, color });
        setTimeout(() => setNotification(null), 3000);
    };

    const { data, isLoading } = useQuery({
        queryKey: ["trash"],
        queryFn: getTrash,
        refetchInterval: 10_000,
    });
    const rows = data?.data || [];

    const restoreMutation = useMutation({
        mutationFn: (id) => restoreFromTrash(id),
        onMutate: (id) => setRestoringId(id),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ["trash"] });
            showNotif(t.trash.restored, "#22c55e");
        },
        onSettled: () => setRestoringId(null),
    });

    const purgeMutation = useMutation({
        mutationFn: purgeTrash,
        onSuccess: (res) => {
            queryClient.invalidateQueries({ queryKey: ["trash"] });
            showNotif(t.trash.purged(res?.data?.deleted ?? 0), "#ef4444");
        },
    });

    const handlePurge = () => {
        if (window.confirm(t.trash.purgeConfirm)) {
            purgeMutation.mutate();
        }
    };

    return (
        <div>
            {/* ── Page header ── */}
            <div className="page-header">
                <div>
                    <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 12 }}>
                        <span style={{
                            fontSize: 28,
                            filter: "drop-shadow(0 0 8px rgba(239,68,68,0.5))",
                        }}></span>
                        {t.trash.pageTitle}
                    </div>
                    <div className="page-subtitle">{t.trash.subtitle(rows.length)}</div>
                </div>

                {rows.length > 0 && (
                    <button
                        id="trash-purge-btn"
                        className="btn btn-sm"
                        onClick={handlePurge}
                        disabled={purgeMutation.isPending}
                        style={{
                            background: "rgba(239,68,68,0.12)",
                            border: "1px solid rgba(239,68,68,0.4)",
                            color: "#ef4444",
                            fontWeight: 600,
                            cursor: purgeMutation.isPending ? "wait" : "pointer",
                            transition: "all 0.2s",
                            padding: "8px 18px",
                            borderRadius: 8,
                        }}
                        onMouseEnter={(e) => {
                            e.currentTarget.style.background = "rgba(239,68,68,0.22)";
                        }}
                        onMouseLeave={(e) => {
                            e.currentTarget.style.background = "rgba(239,68,68,0.12)";
                        }}
                    >
                        {purgeMutation.isPending ? t.trash.purging : `${t.trash.purge}`}
                    </button>
                )}
            </div>

            {/* ── 30-day info banner ── */}
            <div style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                background: "rgba(245,158,11,0.08)",
                border: "1px solid rgba(245,158,11,0.25)",
                borderRadius: 10,
                padding: "10px 18px",
                marginBottom: 20,
                fontSize: 13,
                color: "#d97706",
            }}>
                <span style={{ fontSize: 18 }}></span>
                <span>
                    {t.trash.infoBanner}
                </span>
            </div>

            {/* ── Content ── */}
            {isLoading ? (
                <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
                    <div className="spinner" />
                </div>
            ) : rows.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-state-icon" style={{ fontSize: 56 }}></div>
                    <h3>{t.trash.emptyTitle}</h3>
                    <p style={{ maxWidth: 420 }}>{t.trash.emptyDesc}</p>
                </div>
            ) : (
                <div className="table-wrapper">
                    <table>
                        <thead>
                            <tr>
                                <th>{t.trash.colScreenshot}</th>
                                <th>{t.trash.colClass}</th>
                                <th>{t.trash.colConfidence}</th>
                                <th>{t.trash.colCamera}</th>
                                <th>{t.trash.colLocation}</th>
                                <th>{t.trash.colRejectedAt}</th>
                                <th>{t.trash.colExpiresIn}</th>
                                <th style={{ textAlign: "center" }}></th>
                            </tr>
                        </thead>
                        <tbody>
                            {rows.map((d) => {
                                const effectiveClass = d.operator_correction || d.class_name;
                                const label = t.classLabels[effectiveClass] || effectiveClass;
                                const isRestoring = restoringId === d.id;
                                const daysLeft = getDaysLeft(d.reviewed_at);

                                return (
                                    <tr key={d.id} style={{
                                        opacity: daysLeft === 0 ? 0.6 : 1,
                                        transition: "opacity 0.2s",
                                    }}>
                                        <td>
                                            <img
                                                src={getScreenshotUrl(d.id)}
                                                className="screenshot-thumb"
                                                alt=""
                                                onError={(e) => { e.target.style.display = "none"; }}
                                                style={{ filter: "grayscale(40%)" }}
                                            />
                                        </td>
                                        <td style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "#9ca3af" }}>
                                            {label}
                                        </td>
                                        <td style={{
                                            fontFamily: "var(--font-mono)",
                                            color: "#6b7280",
                                        }}>
                                            {Math.round(d.confidence * 100)}%
                                        </td>
                                        <td style={{ color: "var(--text-secondary)" }}>{d.camera_name}</td>
                                        <td style={{ color: "var(--text-secondary)", fontSize: 12 }}>
                                            {d.camera_location || "—"}
                                        </td>
                                        <td style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                                            {d.reviewed_at
                                                ? new Date(d.reviewed_at).toLocaleString("uk-UA", { timeZone: "Europe/Kyiv" })
                                                : "—"}
                                        </td>
                                        <td>
                                            <ExpiryBadge reviewedAt={d.reviewed_at} t={t} />
                                        </td>
                                        <td style={{ textAlign: "center" }}>
                                            <button
                                                id={`restore-btn-${d.id}`}
                                                className="btn btn-sm"
                                                disabled={isRestoring}
                                                onClick={() => restoreMutation.mutate(d.id)}
                                                style={{
                                                    background: "rgba(34,197,94,0.1)",
                                                    border: "1px solid rgba(34,197,94,0.35)",
                                                    color: "#22c55e",
                                                    fontWeight: 600,
                                                    fontSize: 12,
                                                    padding: "5px 14px",
                                                    borderRadius: 6,
                                                    cursor: isRestoring ? "wait" : "pointer",
                                                    transition: "all 0.15s",
                                                    whiteSpace: "nowrap",
                                                }}
                                                onMouseEnter={(e) => {
                                                    e.currentTarget.style.background = "rgba(34,197,94,0.2)";
                                                }}
                                                onMouseLeave={(e) => {
                                                    e.currentTarget.style.background = "rgba(34,197,94,0.1)";
                                                }}
                                            >
                                                {isRestoring ? t.trash.restoring : `↩ ${t.trash.restore}`}
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                </div>
            )}

            {/* ── Toast notification ── */}
            {notification && (
                <div style={{
                    position: "fixed",
                    bottom: 32,
                    left: "50%",
                    transform: "translateX(-50%)",
                    background: "#1a1a2e",
                    border: `1px solid ${notification.color}`,
                    borderLeft: `4px solid ${notification.color}`,
                    borderRadius: 8,
                    padding: "12px 24px",
                    color: notification.color,
                    fontWeight: 700,
                    fontSize: 14,
                    boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
                    zIndex: 9999,
                    animation: "slideUp 0.3s ease-out",
                }}>
                    {notification.msg}
                </div>
            )}

            <style>{`
                @keyframes slideUp {
                    from { transform: translateX(-50%) translateY(20px); opacity: 0; }
                    to   { transform: translateX(-50%) translateY(0);    opacity: 1; }
                }
            `}</style>
        </div>
    );
}
