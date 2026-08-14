"""
问答接口 — POST /api/chat

接收用户问题，走完完整问答链路：
问题向量化 → 语义检索 → Prompt 拼接 → LLM 生成 → 引用提取

路由层保持轻薄：只做参数校验和调用 services。
"""

import logging
from time import perf_counter

from fastapi import APIRouter, HTTPException
from backend.models.schemas import ChatRequest, ChatResponse
from backend.config import settings
from backend.services.retriever import (
    retrieve_with_trace,
    format_retrieval_context,
)
from backend.services.retrieval_trace import finalize_chat_trace
from backend.services.llm import ask, ask_with_history, rewrite_followup
from backend.services.conversation_context import bounded_history, format_history
from backend.services.conversation_store import (
    ConversationError,
    begin_chat,
    complete_chat,
    complete_history,
    fail_chat,
)
from backend.services.tag_store import (
    TagValidationError,
    normalize_tag_path,
    resolve_document_ids,
)
from backend.utils.citation import extract_citations

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def _normalize_tag_filter(tags: list[str]) -> list[str]:
    """规范化标签筛选列表；非法路径抛 422。"""
    try:
        return sorted({normalize_tag_path(tag) for tag in tags})
    except TagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    问答接口：接收自然语言问题，返回答案 + 引用来源。

    流程：
    1. retriever.retrieve(question) → 语义检索 Top K 文本块
    2. retriever.format_retrieval_context(results) → 拼接上下文
    3. llm.ask(question, context) → DeepSeek 生成答案
    4. citation.extract_citations(answer, results) → 提取引用
    5. 返回 ChatResponse（answer + citations）
    """
    total_started = perf_counter()
    question = request.question.strip()

    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    if len(question) > settings.CONVERSATION_MESSAGE_MAX_CHARS:
        raise HTTPException(status_code=422, detail="问题超过最大长度")

    tag_filter = _normalize_tag_filter(request.tags)
    allowed_document_ids = (
        resolve_document_ids(tag_filter) if tag_filter else None
    )

    persistent = request.conversation_id is not None
    history_rows: list[dict] = []
    user_message_id = None
    standalone_question = question
    if persistent:
        if not request.request_id:
            raise HTTPException(status_code=422, detail="持久化对话必须提供 request_id")
        try:
            started = begin_chat(
                conversation_id=request.conversation_id,
                request_id=request.request_id,
                question=question,
                expected_last_message_id=request.expected_last_message_id,
                retry_failed=request.retry_failed,
            )
        except ConversationError as error:
            raise HTTPException(
                status_code=409 if error.code != "conversation_not_found" else 404,
                detail={"code": error.code, "message": str(error)},
            ) from error
        if "replay" in started:
            return ChatResponse(**started["replay"], trace=None)
        user_message_id = started["user_message_id"]
        history_rows = complete_history(request.conversation_id)
        if history_rows:
            history_text = format_history(bounded_history(history_rows))
            try:
                standalone_question = rewrite_followup(question, history_text)
            except Exception as error:
                logger.warning(
                    "Follow-up rewrite failed: conversation_id=%s request_id=%s error=%s",
                    request.conversation_id,
                    request.request_id,
                    type(error).__name__,
                )
                standalone_question = question

    # 1. 语义检索
    try:
        retrieval = retrieve_with_trace(
            standalone_question,
            allowed_document_ids=allowed_document_ids,
            tag_filter=tag_filter,
        )
    except Exception as error:
        if persistent:
            fail_chat(request.request_id, "retrieval_error")
        raise HTTPException(status_code=500, detail="检索执行失败") from error
    retrieval_results = retrieval.results

    if not retrieval_results:
        # 没有检索到任何相关内容
        trace = finalize_chat_trace(
            retrieval.trace,
            context="",
            context_formatting_ms=0.0,
            llm_generation_ms=0.0,
            total_ms=(perf_counter() - total_started) * 1000,
        )
        if tag_filter:
            answer = (
                "当前筛选范围内未找到与您问题相关的文档内容。"
                "请尝试调整或清除标签筛选后重试。"
            )
        else:
            answer = (
                "未找到与您问题相关的文档内容。"
                "请确认已上传相关文档，或尝试换个问法。"
            )
        response_fields = {}
        if persistent:
            try:
                response_fields = complete_chat(
                    request_id=request.request_id,
                    standalone_question=standalone_question,
                    answer=answer,
                    citations=[],
                )
            except Exception as error:
                fail_chat(request.request_id, "persistence_error")
                raise HTTPException(
                    status_code=500,
                    detail="对话保存失败",
                ) from error
        return ChatResponse(
            answer=answer,
            citations=[],
            trace=trace,
            standalone_question=standalone_question if persistent else None,
            **response_fields,
        )

    # 2. 格式化上下文
    context_started = perf_counter()
    context = format_retrieval_context(retrieval_results)
    context_ms = (perf_counter() - context_started) * 1000

    # 3. 调用 LLM 生成答案
    llm_started = perf_counter()
    try:
        if persistent:
            history_text = format_history(bounded_history(history_rows))
            answer = ask_with_history(
                question,
                standalone_question,
                context,
                history_text,
            )
        else:
            answer = ask(question, context)
    except Exception as e:
        if persistent:
            fail_chat(request.request_id, "llm_error")
        raise HTTPException(
            status_code=502,
            detail=f"LLM 调用失败: {str(e)}"
        )
    llm_ms = (perf_counter() - llm_started) * 1000

    # 4. 提取引用来源
    citations = extract_citations(
        answer, retrieval_results,
        preview_enabled=settings.CITATION_PREVIEW_ENABLED,
    )
    trace = finalize_chat_trace(
        retrieval.trace,
        context=context,
        context_formatting_ms=context_ms,
        llm_generation_ms=llm_ms,
        total_ms=(perf_counter() - total_started) * 1000,
    )

    response_fields = {}
    if persistent:
        try:
            response_fields = complete_chat(
                request_id=request.request_id,
                standalone_question=standalone_question,
                answer=answer,
                citations=citations,
            )
        except Exception as error:
            fail_chat(request.request_id, "persistence_error")
            raise HTTPException(status_code=500, detail="对话保存失败") from error
    return ChatResponse(
        answer=answer,
        citations=citations,
        trace=trace,
        standalone_question=standalone_question if persistent else None,
        **response_fields,
    )
