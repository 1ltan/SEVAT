CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- OPERATORS
CREATE TABLE IF NOT EXISTS operators (
    id           SERIAL PRIMARY KEY,
    username     VARCHAR(100) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role         VARCHAR(50) NOT NULL DEFAULT 'operator',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_operators_username ON operators(username);

-- CAMERAS
CREATE TABLE IF NOT EXISTS cameras (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(200) NOT NULL,
    stream_url    TEXT NOT NULL,
    location_name VARCHAR(200),
    latitude      FLOAT,
    longitude     FLOAT,
    added_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_cameras_is_active ON cameras(is_active);

-- DETECTIONS
CREATE TABLE IF NOT EXISTS detections (
    id                 SERIAL PRIMARY KEY,
    camera_id          INTEGER NOT NULL REFERENCES cameras(id) ON DELETE CASCADE,
    detected_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    class_name         VARCHAR(100) NOT NULL,
    confidence         FLOAT NOT NULL,
    screenshot_path    TEXT,
    status             VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                           CHECK (status IN ('PENDING','CONFIRMED','ARCHIVED','TRASH')),
    operator_correction VARCHAR(100),
    operator_id        INTEGER REFERENCES operators(id) ON DELETE SET NULL,
    reviewed_at        TIMESTAMPTZ,
    threat_level       VARCHAR(20),
    threat_reasoning   TEXT
);
CREATE INDEX IF NOT EXISTS idx_detections_status        ON detections(status);
CREATE INDEX IF NOT EXISTS idx_detections_camera_id     ON detections(camera_id);
CREATE INDEX IF NOT EXISTS idx_detections_detected_at   ON detections(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_detections_class_name    ON detections(class_name);

-- AGENT MESSAGES
CREATE TABLE IF NOT EXISTS agent_messages (
    id         SERIAL PRIMARY KEY,
    session_id UUID NOT NULL,
    role       VARCHAR(20) NOT NULL,
    content    TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_messages_session ON agent_messages(session_id, created_at);

-- AGENT EMBEDDINGS
CREATE TABLE IF NOT EXISTS agent_embeddings (
    id           SERIAL PRIMARY KEY,
    detection_id INTEGER NOT NULL REFERENCES detections(id) ON DELETE CASCADE,
    embedding    vector(768) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_embeddings_detection ON agent_embeddings(detection_id);