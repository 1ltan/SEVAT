import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getDetections, patchDetection, getScreenshotUrl } from "../api/client";
import { useLanguage } from "../context/LanguageContext";

function konfidenceColor(c) {
    if (c >= 0.7) return "var(--accent)";
    if (c >= 0.4) return "var(--warning)";
    return "var(--text-secondary)";
}

function DetectionCard({ det, prevIds, onImageClick }) {
    const { t } = useLanguage();
    const qc = useQueryClient();
    const [showCorrect, setShowCorrect] = useState(false);
    const [correction, setCorrection] = useState("");
    const isNew = !prevIds.has(det.id);

    const CLASS_OPTIONS = [
        { value: "APC", label: t.classLabels.APC },
        { value: "IFV", label: t.classLabels.IFV },
        { value: "TANK", label: t.classLabels.TANK },
        { value: "CAR", label: t.classLabels.CAR },
        { value: "TRUCK", label: t.classLabels.TRUCK },
        { value: "ART", label: t.classLabels.ART },
        { value: "MLRS", label: t.classLabels.MLRS },
    ];

    const mut = useMutation({
        mutationFn: ({ action, correction }) => patchDetection(det.id, { action, correction }),
        onSuccess: () => qc.invalidateQueries(["pending"]),
    });

    return (
        <div className={`detection-card pending ${isNew ? "card-new" : ""}`}>
            <img
                src={getScreenshotUrl(det.id)}
                className="screenshot-thumb"
                alt="screenshot"
                onClick={onImageClick}
                style={{ cursor: "zoom-in" }}
                onError={(e) => { e.target.style.display = "none"; }}
            />
            <div className="detection-info">
                <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <div className="detection-class">
                        {t.classLabels[det.class_name] || det.class_name}
                    </div>
                    <span style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 14,
                        fontWeight: 700,
                        color: konfidenceColor(det.confidence),
                    }}>
                        {Math.round(det.confidence * 100)}%
                    </span>
                    {det.threat_level && (
                        <span className={`badge badge-${det.threat_level?.toLowerCase()}`}>
                             {det.threat_level}
                        </span>
                    )}
                </div>
                <div className="detection-meta">
                    {det.camera_name} ·  {det.camera_location || "—"}<br />
                    {new Date(det.detected_at).toLocaleString("uk-UA", { timeZone: "Europe/Kyiv" })}
                </div>
                {det.threat_reasoning && (
                    <div style={{ fontSize: 11, color: "var(--warning)", marginTop: 6, fontStyle: "italic" }}>
                        {det.threat_reasoning}
                    </div>
                )}
                <div className="detection-actions">
                    <button
                        className="btn btn-success btn-sm"
                        onClick={() => mut.mutate({ action: "confirm" })}
                        disabled={mut.isPending}
                    > {t.alerts.confirm}</button>
                    <button
                        className="btn btn-danger btn-sm"
                        onClick={() => mut.mutate({ action: "reject" })}
                        disabled={mut.isPending}
                    > {t.alerts.reject}</button>
                    <button
                        className="btn btn-warning btn-sm"
                        onClick={() => setShowCorrect(!showCorrect)}
                    > {t.alerts.correct}</button>
                </div>
                {showCorrect && (
                    <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                        <select
                            className="select"
                            value={correction}
                            onChange={(e) => setCorrection(e.target.value)}
                            style={{ maxWidth: 180 }}
                        >
                            <option value="">{t.alerts.selectClass}</option>
                            {CLASS_OPTIONS.map(o => (
                                <option key={o.value} value={o.value}>{o.label}</option>
                            ))}
                        </select>
                        <button
                            className="btn btn-primary btn-sm"
                            disabled={!correction || mut.isPending}
                            onClick={() => { mut.mutate({ action: "correct", correction }); setShowCorrect(false); }}
                        >{t.alerts.save}</button>
                    </div>
                )}
            </div>
        </div>
    );
}

export default function Alerts() {
    const { t } = useLanguage();
    const [prevIds] = useState(new Set());
    const [selectedImage, setSelectedImage] = useState(null);
    const { data, isLoading } = useQuery({
        queryKey: ["pending"],
        queryFn: () => getDetections({ status: "PENDING" }),
        refetchInterval: 3000,
    });

    const detections = data?.data || [];

    const currentIds = new Set(detections.map(d => d.id));
    if (prevIds.size > 0) { }
    currentIds.forEach(id => prevIds.add(id));

    return (
        <div>
            <div className="page-header">
                <div>
                    <div className="page-title">{t.alerts.pageTitle}</div>
                    <div className="page-subtitle">
                        {t.alerts.subtitle(detections.length)}
                    </div>
                </div>
                <span className="badge badge-pending" style={{ fontSize: 14, padding: "4px 12px" }}>
                    {detections.length} {t.alerts.pendingBadge}
                </span>
            </div>

            {isLoading ? (
                <div style={{ display: "flex", justifyContent: "center", padding: 60 }}>
                    <div className="spinner" />
                </div>
            ) : detections.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-state-icon"></div>
                    <h3>{t.alerts.emptyTitle}</h3>
                    <p>{t.alerts.emptyDesc}</p>
                </div>
            ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                    {detections.map(det => (
                        <DetectionCard key={det.id} det={det} prevIds={prevIds} onImageClick={() => setSelectedImage(getScreenshotUrl(det.id))} />
                    ))}
                </div>
            )}

            {/* Fullscreen Image Modal */}
            {selectedImage && (
                <div
                    style={{
                        position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
                        backgroundColor: "rgba(0,0,0,0.85)",
                        display: "flex", justifyContent: "center", alignItems: "center",
                        zIndex: 9999, cursor: "zoom-out"
                    }}
                    onClick={() => setSelectedImage(null)}
                >
                    <img
                        src={selectedImage}
                        style={{
                            maxWidth: "90vw", maxHeight: "90vh",
                            objectFit: "contain", borderRadius: 8,
                            boxShadow: "0 10px 40px rgba(0,0,0,0.5)"
                        }}
                        alt="Enlarged screenshot"
                    />
                </div>
            )}
        </div>
    );
}
