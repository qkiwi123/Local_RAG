"""持久化对话 CRUD API。"""

from fastapi import APIRouter, HTTPException

from backend.models.schemas import (
    ConversationCreate,
    ConversationDetail,
    ConversationSummary,
    ConversationUpdate,
)
from backend.services.conversation_store import (
    ConversationError,
    create_conversation,
    delete_conversation,
    get_conversation,
    list_conversations,
    rename_conversation,
)


router = APIRouter(tags=["conversations"])


@router.post("/conversations", response_model=ConversationDetail)
async def create(payload: ConversationCreate = ConversationCreate()):
    return create_conversation(payload.title)


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_all():
    return list_conversations()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_one(conversation_id: str):
    conversation = get_conversation(conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conversation


@router.patch("/conversations/{conversation_id}", response_model=ConversationDetail)
async def rename(conversation_id: str, payload: ConversationUpdate):
    try:
        return rename_conversation(conversation_id, payload.title)
    except ConversationError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.delete("/conversations/{conversation_id}")
async def delete(conversation_id: str):
    if not delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"status": "success", "conversation_id": conversation_id}
