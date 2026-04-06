import React, { useEffect, useRef, useState } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Navbar from "./components/Navbar";
import Live from "./pages/Live";
import Alerts from "./pages/Alerts";
import Archive from "./pages/Archive";
import Analytics from "./pages/Analytics";
import Agent from "./pages/Agent";
import Trash from "./pages/Trash";
import { LanguageProvider, useLanguage } from "./context/LanguageContext";

// Toast notification component
function Toast({ alert, onDismiss }) {
    const { t } = useLanguage();

    useEffect(() => {
        const timer = setTimeout(onDismiss, 7000);
        return () => clearTimeout(timer);
    }, [onDismiss]);

    const pct = alert.confidence_pct;
    const color = pct >= 70 ? "#ef4444" : pct >= 40 ? "#f59e0b" : "#6b7280";
    const label = t.classLabels[alert.class_name] || alert.class_name;

    return (
        <div style={{
            background: "#1a1a2e",
            border: `1px solid ${color}`,
            borderLeft: `4px solid ${color}`,
            borderRadius: 8,
            padding: "12px 16px",
            minWidth: 300,
            maxWidth: 420,
            boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
            cursor: "pointer",
            animation: "slideIn 0.3s ease-out",
        }} onClick={onDismiss}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ fontWeight: 700, fontSize: 14, color: color }}>
                    ⚠ {t.toast.detected}: {label}
                </div>
                <span style={{ color: "#666", fontSize: 18, lineHeight: 1 }}>✕</span>
            </div>
            <div style={{ fontSize: 12, color: "#ccc", marginTop: 4 }}>
                <span style={{ color: color, fontWeight: 700 }}>{pct}%</span>
                {" · "}{alert.camera_name}
                {alert.camera_location ? ` · ${alert.camera_location}` : ""}
            </div>
            <div style={{ fontSize: 11, color: "#888", marginTop: 2 }}>
                {new Date(alert.detected_at).toLocaleTimeString("uk-UA")}
                {" · "}
                <span style={{ color: alert.status === "ARCHIVED" ? "#22c55e" : "#f59e0b" }}>
                    {alert.status === "ARCHIVED" ? t.toast.autoConfirmed : t.toast.needsReview}
                </span>
            </div>
        </div>
    );
}

// Alert WebSocket hook
function useAlertSocket(onAlert) {
    const wsRef = useRef(null);
    const callbackRef = useRef(onAlert);
    callbackRef.current = onAlert;

    useEffect(() => {
        function connect() {
            const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
            const ws = new WebSocket(`${proto}//${window.location.host}/ws/alerts`);
            wsRef.current = ws;

            ws.onmessage = (ev) => {
                try {
                    const data = JSON.parse(ev.data);
                    if (data.type === "detection") {
                        callbackRef.current(data);
                    }
                } catch (_) { }
            };

            ws.onclose = () => {
                setTimeout(connect, 4000);
            };
        }
        connect();
        return () => { wsRef.current?.close(); };
    }, []);
}

// Inner app
function AppInner() {
    const { t } = useLanguage();
    const [toasts, setToasts] = useState([]);

    const handleAlert = (alert) => {
        const id = Date.now() + Math.random();
        setToasts(prev => [...prev.slice(-4), { id, alert }]);

        if (Notification.permission === "granted") {
            const label = t.classLabels[alert.class_name] || alert.class_name;
            new Notification(`${t.toast.detected}: ${label} (${alert.confidence_pct}%)`, {
                body: `${alert.camera_name} · ${alert.camera_location || ""}`,
                icon: "/favicon.ico",
                tag: `det-${alert.detection_id}`,
            });
        }
    };

    useAlertSocket(handleAlert);

    useEffect(() => {
        if (Notification.permission === "default") {
            Notification.requestPermission();
        }
    }, []);

    const dismissToast = (id) => setToasts(prev => prev.filter(t => t.id !== id));

    return (
        <BrowserRouter>
            <div className="app-layout">
                <Navbar />
                <main className="main-content">
                    <Routes>
                        <Route path="/" element={<Navigate to="/live" replace />} />
                        <Route path="/live" element={<Live />} />
                        <Route path="/alerts" element={<Alerts />} />
                        <Route path="/archive" element={<Archive />} />
                        <Route path="/analytics" element={<Analytics />} />
                        <Route path="/agent" element={<Agent />} />
                        <Route path="/trash" element={<Trash />} />
                    </Routes>
                </main>
            </div>

            {/* Toast notification stack */}
            <div style={{
                position: "fixed", bottom: 24, right: 24,
                display: "flex", flexDirection: "column", gap: 10,
                zIndex: 9999,
            }}>
                {toasts.map(({ id, alert }) => (
                    <Toast key={id} alert={alert} onDismiss={() => dismissToast(id)} />
                ))}
            </div>

            <style>{`
                @keyframes slideIn {
                    from { transform: translateX(120%); opacity: 0; }
                    to   { transform: translateX(0);    opacity: 1; }
                }
            `}</style>
        </BrowserRouter>
    );
}

export default function App() {
    return (
        <LanguageProvider>
            <AppInner />
        </LanguageProvider>
    );
}
