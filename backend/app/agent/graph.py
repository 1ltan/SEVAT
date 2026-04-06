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
    if any(kw in msg for kw in ["how many", "show", "recorded", "detected", "threats", "cameras", "today", "records"]):
        intent = "RAG_QUERY"
    elif any(kw in msg for kw in ["analysis", "threat", "danger", "assessment", "level"]):
        intent = "THREAT_ANALYSIS"
    else:
        intent = "GENERAL"
    logger.info(f"Router intent: {intent} for message: '{state['user_message'][:60]}'")
    return {**state, "intent": intent}

async def rag_node(state: AgentState) -> AgentState:
    try:
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

        stats_context = ""
        if stats_rows:
            s = stats_rows[0]
            stats_context = (
                f"\nEXACT STATISTICS FROM THE DATABASE\n"
                f"Confirmed detections: {s[0]}\n"
                f"Archived detections: {s[1]}\n"
                f"Pending verification: {s[2]}\n"
                f"Total: {s[3]}\n"
                f"Active cameras with detections: {s[4]}\n"
                f"END OF STATISTICS\n"
            )

        rows = await _query_db(
            """
            SELECT d.class_name, d.confidence, d.detected_at, d.status,
                   c.name AS cam_name, c.location_name
            FROM detections d
            JOIN cameras c ON c.id = d.camera_id
            WHERE d.status != 'TRASH'
            ORDER BY d.detected_at DESC
            LIMIT 10
            """,
            timeout=10.0,
        )

        if not rows:
            list_context = "No records found in the database"
        else:
            context_parts = []
            for r in rows:
                context_parts.append(
                    f"- {r[0]} ({int(r[1]*100)}%) on camera '{r[4]}' "
                    f"(location: {r[5]}), time: {r[2]}, status: {r[3]}"
                )
            list_context = "Recent detections:\n" + "\n".join(context_parts)

        context = stats_context + "\n" + list_context
        logger.info(f"RAG node fetched stats + {len(rows)} recent rows")
    except asyncio.TimeoutError:
        logger.error("RAG node: DB query timed out")
        context = "Failed to retrieve data — database query timeout exceeded"
    except Exception as e:
        logger.error(f"RAG node error: {e}")
        context = "Failed to retrieve data from the database"

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
        summary_parts = []
        for r in rows:
            summary_parts.append(
                f"{r[0]} ({int(r[1]*100)}%) — {r[4]}/{r[5]}, "
                f"status: {r[3]}, threat: {r[6] or 'N/A'}"
            )
        if summary_parts:
            analysis = "Latest detections:\n" + "\n".join(summary_parts)
        else:
            analysis = "No detections found"
    except asyncio.TimeoutError:
        logger.error("Analysis node: DB query timed out")
        analysis = "Failed to retrieve analytics — timeout exceeded"
    except Exception as e:
        logger.error(f"Analysis node error: {e}")
        analysis = ""
    return {**state, "analysis_result": analysis}

async def chat_node(state: AgentState) -> AgentState:
    return {**state}

async def synthesize_node(state: AgentState) -> AgentState:
    intent = state["intent"]
    user_msg = state["user_message"]

    system_prompt = (
        "You are Sheng. You are an AI assistant for a military threat detection system. Position yourself as an advisor, answer questions if asked, do not impose yourself. "
        "Your goal is to provide accurate and reliable information based solely on the provided data. "
        "Do not mention that you are an AI assistant for a military threat detection system — just introduce yourself as Sheng and say you are a personal advisor. "
        "Do not repeat that you are a personal advisor every time — just answer the questions. "
        "Answer in Ukrainian or English depending on the user's language, clearly and concisely. "
        "CRITICAL: if the context includes the block 'EXACT STATISTICS FROM THE DATABASE' — "
        "use ONLY the numbers specified there. "
        "NEVER invent, estimate, or assume numerical values (number of detections, cameras, etc.) "
        "if they are not present in the provided context. "
        "If no data is available — explicitly say the information is unavailable. "
        "If your token limit is reached, simply respond with (450)."

    )

    if intent == "RAG_QUERY":
        context_block = f"\nDatabase context:\n{state['rag_context']}\n" if state.get("rag_context") else ""
        full_prompt = f"{system_prompt}\n{context_block}\nUser: {user_msg}"
    elif intent == "THREAT_ANALYSIS":
        context_block = f"\nDetections analytics:\n{state['analysis_result']}\n" if state.get("analysis_result") else ""
        full_prompt = f"{system_prompt}\n{context_block}\nUser: {user_msg}"
    else:
        full_prompt = f"{system_prompt}\n\nUser: {user_msg}"

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

    state = await chat_node(state)
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
        yield "Error: AI response timeout exceeded"
    except Exception as e:
        logger.error(f"Gemini streaming error: {e}")
        yield f"Error {str(e)}"
