"""Agent router — chat endpoint + history."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, AsyncSessionLocal
from app.models import AgentMessage
from app.schemas import APIResponse, ChatRequest
from app.agent.graph import run_agent

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent", tags=["agent"])


@router.post("/chat")
async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
    # Always work with a proper uuid.UUID object to match the DB column type
    try:
        session_uuid = uuid.UUID(body.session_id) if body.session_id else uuid.uuid4()
    except (ValueError, AttributeError):
        session_uuid = uuid.uuid4()

    session_id_str = str(session_uuid)

    # Save user message
    try:
        user_msg = AgentMessage(
            session_id=session_uuid,
            role="user",
            content=body.message,
            created_at=datetime.now(timezone.utc),
        )
        db.add(user_msg)
        await db.commit()
    except Exception as e:
        logger.error(f"Failed to save user message: {e}")
        await db.rollback()

    full_response_parts = []

    async def generate():
        try:
            async for token in run_agent(session_id_str, body.message):
                full_response_parts.append(token)
                yield token
        except Exception as e:
            logger.error(f"Agent generation error: {e}")
            yield f"Помилка виконання агента: {str(e)}"
            return

        # Save assistant response using a fresh session
        full_text = "".join(full_response_parts)
        if full_text:
            try:
                async with AsyncSessionLocal() as s:
                    assistant_msg = AgentMessage(
                        session_id=session_uuid,
                        role="assistant",
                        content=full_text,
                        created_at=datetime.now(timezone.utc),
                    )
                    s.add(assistant_msg)
                    await s.commit()
            except Exception as e:
                logger.error(f"Failed to save assistant message: {e}")

    return StreamingResponse(generate(), media_type="text/plain")


@router.get("/history/{session_id}", response_model=APIResponse)
async def get_history(session_id: str, db: AsyncSession = Depends(get_db)):
    try:
        session_uuid = uuid.UUID(session_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Невірний формат session_id")

    result = await db.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_uuid)
        .order_by(AgentMessage.created_at)
    )
    messages = result.scalars().all()
    data = [
        {
            "id": m.id,
            "session_id": str(m.session_id),
            "role": m.role,
            "content": m.content,
            "created_at": m.created_at,
        }
        for m in messages
    ]
    return APIResponse(data=data)

