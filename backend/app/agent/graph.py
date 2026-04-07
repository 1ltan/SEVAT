import asyncio
import logging
from typing import Any, AsyncGenerator, List, TypedDict

import google.generativeai as genai
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)
genai.configure(api_key=settings.gemini_api_key)
_engine = None


def _get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_size=5,
            max_overflow=10,
        )
    return _engine


async def _query_db(sql: str, params: dict = None, timeout: float = 10.0) -> List[Any]:
    engine = _get_engine()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        result = await asyncio.wait_for(
            session.execute(text(sql), params or {}),
            timeout=timeout,
        )
        return result.fetchall()


class AgentState(TypedDict):
    session_id: str
    user_message: str
    intent: str
    rag_context: str
    analysis_result: str
    final_response: str


async def router_node(state: AgentState) -> AgentState:
    msg = state["user_message"].lower()

    # Ukrainian + English keywords for data queries
    rag_keywords = [
        # Ukrainian
        "скільки", "які", "яких", "яке", "покажи", "показати", "виявлен", "виявлено",
        "зафіксован", "зафіксовано", "об'єкт", "обєкт", "загроз", "камер", "сьогодні",
        "записи", "список", "всі", "все", "усі", "усе", "статистик", "підсумок",
        "інформац", "дані", "данні", "кількість", "всього", "разом", "востаннє",
        "останні", "нещодавно", "зараз", "поточн", "активн",
        # English
        "how many", "show", "recorded", "detected", "threats", "cameras", "today",
        "records", "last", "count", "list", "total", "summary", "stats",
        "incidents", "objects", "info", "all", "current", "active",
    ]

    analysis_keywords = [
        # Ukrainian
        "аналіз", "небезпек", "оцінк", "рівень загроз", "безпек", "ризик", "статус",
        "загрозлив", "критичн",
        # English
        "analysis", "threat", "danger", "assessment", "level", "risk", "security", "status",
    ]

    if any(kw in msg for kw in rag_keywords):
        intent = "RAG_QUERY"
    elif any(kw in msg for kw in analysis_keywords):
        intent = "THREAT_ANALYSIS"
    else:
        # Default: always query DB so we have real data for any question
        intent = "RAG_QUERY"

    logger.info(f"Router intent: {intent} for message: '{state['user_message'][:60]}'")
    return {**state, "intent": intent}


async def rag_node(state: AgentState) -> AgentState:
    try:
        # Overall statistics
        stats_rows = await _query_db(
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'CONFIRMED')  AS confirmed_count,
                COUNT(*) FILTER (WHERE status = 'ARCHIVED')   AS archived_count,
                COUNT(*) FILTER (WHERE status = 'PENDING')    AS pending_count,
                COUNT(*)                                       AS total_non_trash,
                COUNT(DISTINCT camera_id)                      AS active_cameras
            FROM detections
            WHERE status != 'TRASH'
            """,
            timeout=10.0,
        )

        if stats_rows:
            s = stats_rows[0]
            stats_context = (
                f"\n=== EXACT STATISTICS FROM THE DATABASE ===\n"
                f"Confirmed detections: {s[0]}\n"
                f"Archived detections: {s[1]}\n"
                f"Pending verification: {s[2]}\n"
                f"Total (non-trash): {s[3]}\n"
                f"Active cameras with detections: {s[4]}\n"
            )
        else:
            stats_context = "\n=== EXACT STATISTICS FROM THE DATABASE ===\nNo data in database.\n"

        # Per-class breakdown
        class_rows = await _query_db(
            """
            SELECT class_name, COUNT(*) AS cnt
            FROM detections
            WHERE status != 'TRASH'
            GROUP BY class_name
            ORDER BY cnt DESC
            """,
            timeout=10.0,
        )

        if class_rows:
            class_lines = [f"  {r[0]}: {r[1]}" for r in class_rows]
            stats_context += "Breakdown by detected class:\n" + "\n".join(class_lines) + "\n"
        else:
            stats_context += "Breakdown by detected class: no records found in database\n"

        stats_context += "=== END OF STATISTICS ===\n"

        # Recent detections list
        rows = await _query_db(
            """
            SELECT d.class_name, d.confidence, d.detected_at, d.status,
                   c.name AS cam_name, c.location_name
            FROM detections d
            JOIN cameras c ON c.id = d.camera_id
            WHERE d.status != 'TRASH'
            ORDER BY d.detected_at DESC
            LIMIT 20
            """,
            timeout=10.0,
        )

        if not rows:
            list_context = "Recent detections: no records found in the database."
        else:
            context_parts = []
            for r in rows:
                context_parts.append(
                    f"- {r[0]} ({int(r[1]*100)}%) | camera: '{r[4]}' "
                    f"| location: {r[5]} | time: {r[2]} | status: {r[3]}"
                )
            list_context = "Recent detections (up to 20):\n" + "\n".join(context_parts)

        context = stats_context + "\n" + list_context
        logger.info(f"RAG node: stats OK, {len(class_rows)} classes, {len(rows)} recent rows")

    except asyncio.TimeoutError:
        logger.error("RAG node: DB query timed out")
        context = "Database query timed out — data unavailable."
    except Exception as e:
        logger.error(f"RAG node error: {e}")
        context = f"Database error: {e}"

    return {**state, "rag_context": context}


async def analysis_node(state: AgentState) -> AgentState:
    try:
        rows = await _query_db(
            """
            SELECT d.class_name, d.confidence, d.detected_at, d.status,
                   c.name, c.location_name, d.threat_level
            FROM detections d
            JOIN cameras c ON c.id = d.camera_id
            WHERE d.status != 'TRASH'
            ORDER BY d.detected_at DESC
            LIMIT 20
            """,
            timeout=10.0,
        )
        if rows:
            summary_parts = []
            for r in rows:
                summary_parts.append(
                    f"{r[0]} ({int(r[1]*100)}%) — {r[4]}/{r[5]}, "
                    f"status: {r[3]}, threat_level: {r[6] or 'N/A'}"
                )
            analysis = "=== THREAT ANALYSIS DATA (EXACT FROM DB) ===\n" + "\n".join(summary_parts)
        else:
            analysis = "=== THREAT ANALYSIS DATA ===\nNo detections found in database."
    except asyncio.TimeoutError:
        logger.error("Analysis node: DB query timed out")
        analysis = "Database timeout — analytics unavailable."
    except Exception as e:
        logger.error(f"Analysis node error: {e}")
        analysis = f"Database error: {e}"
    return {**state, "analysis_result": analysis}


async def synthesize_node(state: AgentState) -> AgentState:
    intent = state["intent"]
    user_msg = state["user_message"]

    if intent == "RAG_QUERY":
        db_ctx = state.get("rag_context", "No data")
        full_prompt = (
            "Your name is Sheng (Шенг). You are a military AI analyst for the SEVAT system. "
            "Answer ONLY based on the database data below. "
            "Do NOT invent numbers. Reply in the same language as the user's question.\n\n"
            f"[DATABASE DATA]\n{db_ctx}\n[END]\n\n"
            f"User question: {user_msg}\n\nAnswer:"
        )
    elif intent == "THREAT_ANALYSIS":
        db_ctx = state.get("analysis_result", "No data")
        full_prompt = (
            "Your name is Sheng (Шенг). You are a military AI analyst for the SEVAT system. "
            "Analyze ONLY the data below. "
            "Do NOT invent numbers. Reply in the same language as the user's question.\n\n"
            f"[DETECTION DATA]\n{db_ctx}\n[END]\n\n"
            f"User question: {user_msg}\n\nAnswer:"
        )
    else:
        full_prompt = (
            "Your name is Sheng (Шенг). You are a military AI analyst for the SEVAT system. "
            "Answer concisely. Reply in the same language as the user's question.\n\n"
            f"User question: {user_msg}\n\nAnswer:"
        )

    return {**state, "final_response": full_prompt}


async def run_agent(session_id: str, message: str) -> AsyncGenerator[str, None]:
    state: AgentState = {
        "session_id": session_id,
        "user_message": message,
        "intent": "GENERAL",
        "rag_context": "",
        "analysis_result": "",
        "final_response": "",
    }

    state = await router_node(state)

    if state["intent"] == "RAG_QUERY":
        state = await rag_node(state)
    elif state["intent"] == "THREAT_ANALYSIS":
        state = await analysis_node(state)

    state = await synthesize_node(state)

    prompt = state["final_response"]
    model = genai.GenerativeModel(settings.gemini_model)

    try:
        loop = asyncio.get_event_loop()
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: model.generate_content(prompt, stream=True),
            ),
            timeout=30.0,
        )

        def _iter_chunks():
            chunks = []
            for chunk in response:
                if chunk.text:
                    chunks.append(chunk.text)
            return chunks

        chunks = await asyncio.wait_for(
            loop.run_in_executor(None, _iter_chunks),
            timeout=30.0,
        )

        for chunk in chunks:
            yield chunk

    except asyncio.TimeoutError:
        logger.error("Gemini response timed out")
        yield "Помилка: час очікування відповіді вичерпано."
    except Exception as e:
        logger.error(f"Gemini streaming error: {e}")
        yield f"Помилка: {str(e)}"
