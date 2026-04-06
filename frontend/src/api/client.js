import axios from "axios";

const api = axios.create({
    baseURL: "/api",
    timeout: 30_000,
    headers: { "Content-Type": "application/json" },
});

// Response interceptor — return data field
api.interceptors.response.use(
    (res) => res.data,
    (err) => {
        const msg = err.response?.data?.error || err.message || "Помилка запиту";
        return Promise.reject(new Error(msg));
    }
);

// Cameras
export const getCameras = () => api.get("/cameras");
export const addCamera = (data) => api.post("/cameras", data);
export const deleteCamera = (id) => api.delete(`/cameras/${id}`);
export const updateCamera = (id, data) => api.patch(`/cameras/${id}`, data);
export const startCamera = (id) => api.post(`/cameras/${id}/start`);
export const stopCamera = (id) => api.post(`/cameras/${id}/stop`);

// Detections
export const getDetections = (params) => api.get("/detections", { params });
export const patchDetection = (id, data) => api.patch(`/detections/${id}`, data);
export const getScreenshotUrl = (id) => `/api/detections/${id}/screenshot`;

// Archive
export const getArchive = (params) => api.get("/archive", { params });
export const getTrash = () => api.get("/trash");
export const purgeTrash = () => api.delete("/trash/purge");
export const restoreFromTrash = (id) => api.post(`/trash/${id}/restore`);

// Analytics
export const getSummary = () => api.get("/analytics/summary");
export const generateReport = (data) => api.post("/analytics/report", data);
export const generatePdfReport = async (data) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 120_000);
    let response;
    try {
        response = await fetch("/api/analytics/report/pdf", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
            signal: controller.signal,
        });
    } catch (e) {
        clearTimeout(timeoutId);
        if (e.name === "AbortError") throw new Error("Timeout: PDF generation exceeded 120 seconds");
        throw e;
    }
    clearTimeout(timeoutId);
    if (!response.ok) {
        let errMsg = "PDF generation error";
        try {
            const errBody = await response.text();
            const parsed = JSON.parse(errBody);
            errMsg = parsed?.detail || parsed?.error || errMsg;
        } catch (_) { }
        throw new Error(`${response.status}: ${errMsg}`);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    a.href = url;
    a.download = match ? match[1] : "sevat_report.pdf";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
};

// Agent
export const getAgentHistory = (sessionId) => api.get(`/agent/history/${sessionId}`);

export const streamChat = async (sessionId, message, onToken) => {
    const response = await fetch("/api/agent/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, message }),
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let done = false;
    while (!done) {
        const { value, done: d } = await reader.read();
        done = d;
        if (value) onToken(decoder.decode(value, { stream: !done }));
    }
};

export default api;
