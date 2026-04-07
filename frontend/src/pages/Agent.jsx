import React, { useState, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { streamChat, getAgentHistory, getCameras } from "../api/client";
import { useLanguage } from "../context/LanguageContext";

const SESSION_ID_KEY = "sevat_agent_session";

function getOrCreateSession() {
    let id = localStorage.getItem(SESSION_ID_KEY);
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem(SESSION_ID_KEY, id);
    }
    return id;
}

export default function Agent() {
    const { t } = useLanguage();
    const [sessionId] = useState(getOrCreateSession);
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState("");
    const [isStreaming, setIsStreaming] = useState(false);
    const [streamingText, setStreamingText] = useState("");
    const bottomRef = useRef(null);

    const { data: camData } = useQuery({ queryKey: ["cameras"], queryFn: getCameras });
    const cameras = camData?.data || [];

    // Load history
    useEffect(() => {
        getAgentHistory(sessionId).then(res => {
            if (res?.data) setMessages(res.data);
        }).catch(() => { });
    }, [sessionId]);

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }, [messages, streamingText]);

    const sendMessage = async (text) => {
        const msg = text || input.trim();
        if (!msg || isStreaming) return;
        setInput("");
        setIsStreaming(true);
        setStreamingText("");

        const userMsg = { id: Date.now(), role: "user", content: msg, created_at: new Date().toISOString() };
        setMessages(prev => [...prev, userMsg]);

        let full = "";
        try {
            await streamChat(sessionId, msg, (token) => {
                full += token;
                setStreamingText(tk => tk + token);
            });
        } catch (e) {
            full = `${t.agent.errorPrefix}${e.message}`;
            setStreamingText(full);
        }

        const assistantMsg = { id: Date.now() + 1, role: "assistant", content: full, created_at: new Date().toISOString() };
        setMessages(prev => [...prev, assistantMsg]);
        setStreamingText("");
        setIsStreaming(false);
    };

    const handleKeyDown = (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    };

    return (
        <div>
            <div className="page-header">
                <div>
                    <div className="page-title">{t.agent.pageTitle}</div>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    <span style={{
                        fontFamily: "var(--font-mono)", fontSize: 11,
                        background: "var(--bg-card)", border: "1px solid var(--border)",
                        borderRadius: 6, padding: "4px 10px", color: "var(--text-secondary)",
                    }}>
                        session: {sessionId.slice(0, 8)}…
                    </span>
                    <span style={{
                        fontFamily: "var(--font-mono)", fontSize: 11,
                        background: "var(--bg-card)", border: "1px solid var(--border)",
                        borderRadius: 6, padding: "4px 10px", color: "var(--text-secondary)",
                    }}>
                        {t.agent.cameras(cameras.length)}
                    </span>
                    <button className="btn btn-ghost btn-sm" onClick={() => {
                        localStorage.removeItem(SESSION_ID_KEY);
                        window.location.reload();
                    }}>{t.agent.newChat}</button>
                </div>
            </div>

            <div className="chat-container">
                <div className="chat-messages">
                    {messages.length === 0 && !isStreaming && (
                        <div style={{ textAlign: "center", padding: "40px 20px" }}>
                            <div style={{ fontSize: 32, marginBottom: 12 }}></div>
                            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 24 }}>{t.agent.welcomeTitle}</div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 480, margin: "0 auto" }}>
                                {t.agent.examples.map((q, i) => (
                                    <button key={i} className="btn btn-ghost"
                                        style={{ justifyContent: "flex-start", fontFamily: "var(--font-mono)", fontSize: 12 }}
                                        onClick={() => sendMessage(q)}>
                                        &gt; {q}
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}

                    {messages.map(msg => (
                        <div key={msg.id} className={`chat-message ${msg.role}`}>
                            <div style={{
                                width: 32, height: 32, borderRadius: "50%",
                                background: msg.role === "user" ? "var(--accent-dim)" : "var(--bg-card)",
                                border: "1px solid var(--border)",
                                display: "flex", alignItems: "center", justifyContent: "center",
                                fontSize: 14, flexShrink: 0,
                            }}>
                                {msg.role === "user" ? "O" : "S"}
                            </div>
                            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginLeft: 8 }}>
                                    {msg.role === "user" ? "Operator" : "Sheng"}
                                </div>
                                <div className={`chat-bubble ${msg.role}`}>{msg.content}</div>
                            </div>
                        </div>
                    ))}

                    {isStreaming && streamingText && (
                        <div className="chat-message assistant">
                            <div style={{
                                width: 32, height: 32, borderRadius: "50%",
                                background: "var(--bg-card)",
                                border: "1px solid var(--accent-dim)",
                                display: "flex", alignItems: "center", justifyContent: "center",
                                fontSize: 14, flexShrink: 0,
                            }}>S</div>
                            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginLeft: 8 }}>
                                    Sheng
                                </div>
                                <div className="chat-bubble assistant">
                                    {streamingText}<span className="cursor-blink" />
                                </div>
                            </div>
                        </div>
                    )}

                    {isStreaming && !streamingText && (
                        <div className="chat-message assistant">
                            <div style={{
                                width: 32, height: 32, borderRadius: "50%",
                                background: "var(--bg-card)", border: "1px solid var(--border)",
                                display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, flexShrink: 0,
                            }}>S</div>
                            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 4 }}>
                                <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", marginLeft: 8 }}>
                                    Sheng
                                </div>
                                <div className="chat-bubble assistant" style={{ color: "var(--text-secondary)" }}>
                                    <span className="cursor-blink" />
                                </div>
                            </div>
                        </div>
                    )}

                    <div ref={bottomRef} />
                </div>

                <div className="chat-input-row">
                    <textarea
                        className="chat-input"
                        rows={1}
                        placeholder={t.agent.placeholder}
                        value={input}
                        onChange={e => setInput(e.target.value)}
                        onKeyDown={handleKeyDown}
                        disabled={isStreaming}
                        style={{ maxHeight: 120 }}
                    />
                    <button className="btn btn-primary" onClick={() => sendMessage()} disabled={isStreaming || !input.trim()}>
                        {isStreaming ? t.agent.sending : t.agent.send}
                    </button>
                </div>
            </div>
        </div>
    );
}
