"""
LangGraph AI Agent with 5 nodes:
Router → RAG / Analysis / Chat → Response Synthesizer
"""
import asyncio
import logging
from typing import Any, AsyncGenerator, List, TypedDict

import google.generativeai as genai
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)

# ── Configure Gemini ─────────────────────────────────────────────────────────
genai.configure(api_key=settings.gemini_api_key)

# ── Single shared engine (created once, not per-request) ─────────────────────
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
    """Execute a DB query with a timeout to prevent hanging."""
    engine = _get_engine()
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        result = await asyncio.wait_for(
            session.execute(text(sql), params or {}),
            timeout=timeout,
        )
        return result.fetchall()


# ── State ────────────────────────────────────────────────────────────────────
class AgentState(TypedDict):
    session_id: str
    user_message: str
    intent: str  # RAG_QUERY | THREAT_ANALYSIS | GENERAL
    rag_context: str
    analysis_result: str
    final_response: str


# ── Node: Router ──────────────────────────────────────────────────────────────
async def router_node(state: AgentState) -> AgentState:
    msg = state["user_message"].lower()
    if any(kw in msg for kw in ["скільки", "покажи", "зафіксовано", "виявлено", "загроз", "камер", "сьогодні", "записи"]):
        intent = "RAG_QUERY"
    elif any(kw in msg for kw in ["аналіз", "загроза", "небезпека", "оцінка", "рівень"]):
        intent = "THREAT_ANALYSIS"
    else:
        intent = "GENERAL"
    logger.info(f"Router intent: {intent} for message: '{state['user_message'][:60]}'")
    return {**state, "intent": intent}


# ── Node: RAG ─────────────────────────────────────────────────────────────────
async def rag_node(state: AgentState) -> AgentState:
    """Query detections with exact statistics to prevent AI hallucination."""
    try:
        # --- Exact aggregate statistics ---
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
                f"\n=== ТОЧНА СТАТИСТИКА З БД ===\n"
                f"Підтверджені виявлення (CONFIRMED): {s[0]}\n"
                f"Архівні виявлення (ARCHIVED): {s[1]}\n"
                f"Очікують перевірки (PENDING): {s[2]}\n"
                f"Всього (не TRASH): {s[3]}\n"
                f"Активних камер зі знахідками: {s[4]}\n"
                f"=== КІНЕЦЬ СТАТИСТИКИ ===\n"
            )

        # --- Recent detections list ---
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
            list_context = "Записів у базі даних не знайдено."
        else:
            context_parts = []
            for r in rows:
                context_parts.append(
                    f"- {r[0]} ({int(r[1]*100)}%) на камері '{r[4]}' "
                    f"(локація: {r[5]}), час: {r[2]}, статус: {r[3]}"
                )
            list_context = "Останні виявлення:\n" + "\n".join(context_parts)

        context = stats_context + "\n" + list_context
        logger.info(f"RAG node fetched stats + {len(rows)} recent rows")
    except asyncio.TimeoutError:
        logger.error("RAG node: DB query timed out")
        context = "Не вдалося отримати дані — перевищено час очікування запиту до БД."
    except Exception as e:
        logger.error(f"RAG node error: {e}")
        context = "Не вдалося отримати дані з бази."

    return {**state, "rag_context": context}


# ── Node: Analysis ────────────────────────────────────────────────────────────
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
                f"статус: {r[3]}, загроза: {r[6] or 'N/A'}"
            )
        if summary_parts:
            analysis = "Останні виявлення:\n" + "\n".join(summary_parts)
        else:
            analysis = "Виявлень не знайдено."
    except asyncio.TimeoutError:
        logger.error("Analysis node: DB query timed out")
        analysis = "Не вдалося отримати аналітику — перевищено час очікування."
    except Exception as e:
        logger.error(f"Analysis node error: {e}")
        analysis = ""
    return {**state, "analysis_result": analysis}


# ── Node: Chat ────────────────────────────────────────────────────────────────
async def chat_node(state: AgentState) -> AgentState:
    return {**state}


# ── Node: Response Synthesizer ────────────────────────────────────────────────
async def synthesize_node(state: AgentState) -> AgentState:
    intent = state["intent"]
    user_msg = state["user_message"]

    system_prompt = (
        "Тебе звати Шенг. Ти — AI-асистент військової системи виявлення загроз. Позиціонуй себе як радника, відповідай якщо тебе питають, не нав'язуйся. "
        "Твоя мета — надавати точну і достовірну інформацію, спираючись виключно на надані дані. "
        "Не розповідай про те що ти AI-асистент військової системи виявлення загроз, просто представляйся як Шенг і скажи що ти особистий радник"
        "Не говори при кожному разі що ти особистий радник, просто відповідай на питання"
        "Відповідай українською або англійською мовами (в залежності від мови користувача), чітко і лаконічно. "
        "КРИТИЧНО ВАЖЛИВО: якщо у контексті є блок '=== ТОЧНА СТАТИСТИКА З БД ===' — "
        "використовуй ВИКЛЮЧНО ті числа, що вказані там. "
        "НІКОЛИ не вигадуй, не оцінюй і не припускай кількісні показники (кількість виявлень, камер тощо) "
        "якщо вони відсутні в наданому контексті. "
        "Якщо даних немає — прямо скажи що інформація недоступна."
        "У разі якщо твій ліміт токенів закінчився, просто скажи (NOX)"
    )

    if intent == "RAG_QUERY":
        context_block = f"\nКонтекст із бази даних:\n{state['rag_context']}\n" if state.get("rag_context") else ""
        full_prompt = f"{system_prompt}\n{context_block}\nКористувач: {user_msg}"
    elif intent == "THREAT_ANALYSIS":
        context_block = f"\nАналітика виявлень:\n{state['analysis_result']}\n" if state.get("analysis_result") else ""
        full_prompt = f"{system_prompt}\n{context_block}\nКористувач: {user_msg}"
    else:
        full_prompt = f"{system_prompt}\n\nКористувач: {user_msg}"

    return {**state, "final_response": full_prompt}


# ── Graph Runner ──────────────────────────────────────────────────────────────
async def run_agent(session_id: str, message: str) -> AsyncGenerator[str, None]:
    """Run the LangGraph pipeline and stream the final Gemini response."""
    state: AgentState = {
        "session_id": session_id,
        "user_message": message,
        "intent": "GENERAL",
        "rag_context": "",
        "analysis_result": "",
        "final_response": "",
    }

    # Execute nodes
    state = await router_node(state)

    if state["intent"] == "RAG_QUERY":
        state = await rag_node(state)
    elif state["intent"] == "THREAT_ANALYSIS":
        state = await analysis_node(state)

    state = await chat_node(state)
    state = await synthesize_node(state)

    # Stream response from Gemini
    prompt = state["final_response"]
    model = genai.GenerativeModel(settings.gemini_model)

    try:
        loop = asyncio.get_event_loop()

        # Run blocking generate_content in executor to avoid blocking event loop
        response = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                lambda: model.generate_content(prompt, stream=True),
            ),
            timeout=30.0,
        )

        # Iterate chunks in executor to avoid blocking
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
        yield "Помилка: час очікування відповіді від AI вичерпано."
    except Exception as e:
        logger.error(f"Gemini streaming error: {e}")
        yield f"Помилка: {str(e)}"
