"""
问答接口 — POST /api/chat

接收用户问题，走完完整问答链路：
问题向量化 → 语义检索 → Prompt 拼接 → LLM 生成 → 引用提取

路由层保持轻薄：只做参数校验和调用 services。
"""

import asyncio
import json
import logging
from time import perf_counter
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from backend.models.schemas import ChatRequest, ChatResponse
from backend.config import settings
from backend.services.retriever import (
    retrieve_with_trace,
    format_retrieval_context,
)
from backend.services.sparse_index import SparseIndexError
from backend.services.retrieval_trace import finalize_chat_trace
from backend.services.llm import (
    ask,
    ask_stream_async,
    ask_with_history,
    ask_with_history_stream_async,
    build_retrieval_only_answer,
    rewrite_followup,
)
from backend.models.settings_schemas import EffectiveSettingsSnapshot
from backend.services.conversation_context import bounded_history, format_history
from backend.services.conversation_store import (
    ConversationError,
    begin_chat,
    complete_chat,
    complete_history,
    fail_chat,
    save_trace,
)
from backend.services.tag_store import (
    TagValidationError,
    normalize_tag_path,
    resolve_document_ids,
)
from backend.utils.citation import extract_citations

router = APIRouter(tags=["chat"])
logger = logging.getLogger(__name__)


def _runtime_snapshot(request: Request) -> EffectiveSettingsSnapshot:
    manager = getattr(request.app.state, "runtime_settings_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="运行时设置尚未初始化")
    return manager.snapshot()


def _normalize_tag_filter(tags: list[str]) -> list[str]:
    """规范化标签筛选列表；非法路径抛 422。"""
    try:
        return sorted({normalize_tag_path(tag) for tag in tags})
    except TagValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def save_persistent_trace(response_fields: dict, trace) -> None:
    """把 Trace 持久化到助手消息；失败只告警，不影响回答结果。"""
    assistant_message_id = response_fields.get("assistant_message_id")
    if not assistant_message_id or trace is None:
        return
    try:
        save_trace(
            assistant_message_id,
            trace.model_dump(mode="json"),
        )
    except Exception as error:
        logger.warning(
            "Trace persist failed: message_id=%s error=%s",
            assistant_message_id,
            type(error).__name__,
        )


def _complete_chat_with_trace(
    *,
    request_id: str,
    standalone_question: str,
    answer: str,
    citations,
    trace,
) -> dict:
    """提交会话并保存 Trace；persistence 只计 complete_chat 事务。"""
    persistence_started = perf_counter()
    response_fields = complete_chat(
        request_id=request_id,
        standalone_question=standalone_question,
        answer=answer,
        citations=citations,
    )
    if trace is not None:
        trace.timings_ms.persistence = round(
            (perf_counter() - persistence_started) * 1000,
            3,
        )
    save_persistent_trace(response_fields, trace)
    return response_fields


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request):
    """
    问答接口：接收自然语言问题，返回答案 + 引用来源。

    流程：
    1. retriever.retrieve(question) → 语义检索 Top K 文本块
    2. retriever.format_retrieval_context(results) → 拼接上下文
    3. llm.ask(question, context) → DeepSeek 生成答案
    4. citation.extract_citations(answer, results) → 提取引用
    5. 返回 ChatResponse（answer + citations）
    """
    request_started = perf_counter()
    runtime = _runtime_snapshot(http_request)
    llm_called = False
    followup_rewrite_ms = None
    followup_rewrite_status = "not_applicable"
    question = request.question.strip()

    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    if len(question) > settings.CONVERSATION_MESSAGE_MAX_CHARS:
        raise HTTPException(status_code=422, detail="问题超过最大长度")

    tag_filter = _normalize_tag_filter(request.tags)
    allowed_document_ids = (
        await asyncio.to_thread(resolve_document_ids, tag_filter)
        if tag_filter
        else None
    )

    persistent = request.conversation_id is not None
    history_rows: list[dict] = []
    user_message_id = None
    standalone_question = question
    if persistent:
        if not request.request_id:
            raise HTTPException(status_code=422, detail="持久化对话必须提供 request_id")
        try:
            started = await asyncio.to_thread(
                begin_chat,
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
            replay = started["replay"]
            persisted_trace = replay.pop("trace", None)
            return ChatResponse(**replay, trace=persisted_trace)
        user_message_id = started["user_message_id"]
        history_rows = await asyncio.to_thread(
            complete_history,
            request.conversation_id,
        )
        if history_rows and runtime.llm.enabled and runtime.llm.rewrite_enabled:
            history_text = format_history(bounded_history(history_rows))
            rewrite_started = perf_counter()
            try:
                llm_called = True
                standalone_question = await asyncio.to_thread(
                    rewrite_followup,
                    question,
                    history_text,
                    runtime,
                )
                followup_rewrite_status = "succeeded"
            except Exception as error:
                followup_rewrite_status = "failed"
                logger.warning(
                    "Follow-up rewrite failed: conversation_id=%s request_id=%s error=%s",
                    request.conversation_id,
                    request.request_id,
                    type(error).__name__,
                )
                standalone_question = question
            finally:
                followup_rewrite_ms = (
                    perf_counter() - rewrite_started
                ) * 1000
        elif history_rows and runtime.llm.enabled:
            followup_rewrite_status = "disabled"

    # 1. 语义检索
    try:
        retrieval = await asyncio.to_thread(
            retrieve_with_trace,
            standalone_question,
            allowed_document_ids=allowed_document_ids,
            tag_filter=tag_filter,
            runtime_config=runtime.retrieval,
        )
    except SparseIndexError as error:
        # P6-A：Sparse索引不可用（dirty/缺失/损坏/错配）时显式失败，
        # 不静默降级；管理员可将检索模式切换为dense临时恢复。
        if persistent:
            await asyncio.to_thread(
                fail_chat,
                request.request_id,
                "sparse_index_unavailable",
            )
        raise HTTPException(
            status_code=503,
            detail=f"检索索引不可用，请稍后重试或联系管理员（{type(error).__name__}: {error}）",
        ) from error
    except Exception as error:
        if persistent:
            await asyncio.to_thread(
                fail_chat,
                request.request_id,
                "retrieval_error",
            )
        raise HTTPException(status_code=500, detail="检索执行失败") from error
    retrieval_results = retrieval.results

    if not retrieval_results:
        # 没有检索到任何相关内容
        trace = finalize_chat_trace(
            retrieval.trace,
            context="",
            context_formatting_ms=0.0,
            llm_generation_ms=0.0,
            followup_rewrite_ms=followup_rewrite_ms,
            followup_rewrite_status=followup_rewrite_status,
            citation_extraction_ms=None,
            total_ms=(perf_counter() - request_started) * 1000,
            runtime=runtime,
            llm_called=llm_called,
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
                response_fields = await asyncio.to_thread(
                    _complete_chat_with_trace,
                    request_id=request.request_id,
                    standalone_question=standalone_question,
                    answer=answer,
                    citations=[],
                    trace=trace,
                )
            except Exception as error:
                await asyncio.to_thread(
                    fail_chat,
                    request.request_id,
                    "persistence_error",
                )
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
    context = await asyncio.to_thread(
        format_retrieval_context,
        retrieval_results,
    )
    context_ms = (perf_counter() - context_started) * 1000

    # 3. 调用 LLM 生成答案
    if not runtime.llm.enabled:
        answer = build_retrieval_only_answer(len(retrieval_results))
        llm_ms = 0.0
    else:
        llm_started = perf_counter()
        try:
            llm_called = True
            if persistent:
                history_text = format_history(bounded_history(history_rows))
                answer = await asyncio.to_thread(
                    ask_with_history,
                    question,
                    standalone_question,
                    context,
                    history_text,
                    runtime,
                )
            else:
                answer = await asyncio.to_thread(
                    ask,
                    question,
                    context,
                    runtime,
                )
        except Exception as error:
            if persistent:
                await asyncio.to_thread(
                    fail_chat,
                    request.request_id,
                    "llm_error",
                )
            raise HTTPException(
                status_code=502,
                detail=f"LLM 调用失败: {type(error).__name__}",
            ) from error
        llm_ms = (perf_counter() - llm_started) * 1000

    # 4. 提取引用来源
    citation_started = perf_counter()
    citations = await asyncio.to_thread(
        extract_citations,
        answer,
        retrieval_results,
        preview_enabled=settings.CITATION_PREVIEW_ENABLED,
    )
    citation_ms = (perf_counter() - citation_started) * 1000
    trace = finalize_chat_trace(
        retrieval.trace,
        context=context,
        context_formatting_ms=context_ms,
        llm_generation_ms=llm_ms,
        followup_rewrite_ms=followup_rewrite_ms,
        followup_rewrite_status=followup_rewrite_status,
        citation_extraction_ms=citation_ms,
        total_ms=(perf_counter() - request_started) * 1000,
        runtime=runtime,
        llm_called=llm_called,
    )

    response_fields = {}
    if persistent:
        try:
            response_fields = await asyncio.to_thread(
                _complete_chat_with_trace,
                request_id=request.request_id,
                standalone_question=standalone_question,
                answer=answer,
                citations=citations,
                trace=trace,
            )
        except Exception as error:
            await asyncio.to_thread(
                fail_chat,
                request.request_id,
                "persistence_error",
            )
            raise HTTPException(status_code=500, detail="对话保存失败") from error
    return ChatResponse(
        answer=answer,
        citations=citations,
        trace=trace,
        standalone_question=standalone_question if persistent else None,
        **response_fields,
    )


def _ndjson(event: str, **fields) -> str:
    return json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n"


def _trace_dump(trace) -> dict | None:
    return trace.model_dump(mode="json") if trace is not None else None


async def _stream_chat_events(
    request: ChatRequest,
    started: dict,
    standalone_question: str,
    tag_filter: list[str],
    allowed_document_ids,
    request_started: float,
    followup_rewrite_ms: float | None = None,
    followup_rewrite_status: Literal[
        "not_applicable", "disabled", "succeeded", "failed"
    ] = "not_applicable",
    runtime: EffectiveSettingsSnapshot | None = None,
    llm_called: bool = False,
) -> "AsyncGenerator[str, None]":
    """流式问答事件生成器（NDJSON 行）。

    事件序列：meta → delta* → citations → done；运行期失败发 error 事件。
    中断语义甲：客户端断开（GeneratorExit）时按失败处理（fail_chat），
    不保存半截答案。
    """
    persistent = request.conversation_id is not None
    completed = False
    first_delta_at = None
    last_delta_at = None

    def mark_delta() -> None:
        nonlocal first_delta_at, last_delta_at
        now = perf_counter()
        if first_delta_at is None:
            first_delta_at = now
        last_delta_at = now

    try:
        replay = started.get("replay")
        if replay:
            trace = replay.pop("trace", None)
            yield _ndjson(
                "meta",
                conversation_id=replay["conversation_id"],
                request_id=request.request_id,
                user_message_id=replay["user_message_id"],
                assistant_message_id=replay["assistant_message_id"],
                standalone_question=replay["standalone_question"],
            )
            yield _ndjson("delta", text=replay["answer"])
            yield _ndjson(
                "citations",
                citations=replay["citations"],
            )
            completed = True
            yield _ndjson(
                "done",
                assistant_message_id=replay["assistant_message_id"],
                trace=trace,
            )
            return

        user_message_id = started.get("user_message_id")
        yield _ndjson(
            "meta",
            conversation_id=request.conversation_id,
            request_id=request.request_id,
            user_message_id=user_message_id,
            standalone_question=standalone_question,
        )

        try:
            retrieval = await asyncio.to_thread(
                retrieve_with_trace,
                standalone_question,
                allowed_document_ids=allowed_document_ids,
                tag_filter=tag_filter,
                runtime_config=runtime.retrieval if runtime else None,
            )
        except SparseIndexError as error:
            if persistent:
                await asyncio.to_thread(
                    fail_chat,
                    request.request_id,
                    "sparse_index_unavailable",
                )
            yield _ndjson(
                "error",
                code="sparse_index_unavailable",
                message=f"检索索引不可用，请稍后重试（{type(error).__name__}: {error}）",
            )
            return
        except Exception:
            if persistent:
                await asyncio.to_thread(
                    fail_chat,
                    request.request_id,
                    "retrieval_error",
                )
            yield _ndjson(
                "error",
                code="retrieval_error",
                message="检索执行失败",
            )
            return

        retrieval_results = retrieval.results
        if not retrieval_results:
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
            mark_delta()
            trace = finalize_chat_trace(
                retrieval.trace,
                context="",
                context_formatting_ms=0.0,
                llm_generation_ms=0.0,
                followup_rewrite_ms=followup_rewrite_ms,
                followup_rewrite_status=followup_rewrite_status,
                citation_extraction_ms=None,
                time_to_first_delta_ms=(
                    (first_delta_at - request_started) * 1000
                    if first_delta_at is not None
                    else None
                ),
                delta_window_ms=0.0,
                total_ms=(perf_counter() - request_started) * 1000,
                runtime=runtime,
                llm_called=llm_called,
            )
            yield _ndjson("delta", text=answer)
            response_fields = {}
            if persistent:
                response_fields = await asyncio.to_thread(
                    _complete_chat_with_trace,
                    request_id=request.request_id,
                    standalone_question=standalone_question,
                    answer=answer,
                    citations=[],
                    trace=trace,
                )
            yield _ndjson("citations", citations=[])
            completed = True
            yield _ndjson(
                "done",
                assistant_message_id=response_fields.get("assistant_message_id"),
                trace=_trace_dump(trace),
            )
            return

        context_started = perf_counter()
        context = await asyncio.to_thread(
            format_retrieval_context,
            retrieval_results,
        )
        context_ms = (perf_counter() - context_started) * 1000
        collected: list[str] = []
        llm_ms = 0.0
        if runtime is not None and not runtime.llm.enabled:
            piece = build_retrieval_only_answer(len(retrieval_results))
            collected.append(piece)
            mark_delta()
            yield _ndjson("delta", text=piece)
        elif persistent:
            llm_called = True
            history_rows = await asyncio.to_thread(
                complete_history,
                request.conversation_id,
            )
            history_text = format_history(bounded_history(history_rows))
            llm_started = perf_counter()
            pieces = ask_with_history_stream_async(
                request.question.strip(),
                standalone_question,
                context,
                history_text,
                runtime,
            )
            async for piece in pieces:
                collected.append(piece)
                mark_delta()
                yield _ndjson("delta", text=piece)
            llm_ms = (perf_counter() - llm_started) * 1000
        else:
            llm_called = True
            llm_started = perf_counter()
            pieces = ask_stream_async(request.question.strip(), context, runtime)
            async for piece in pieces:
                collected.append(piece)
                mark_delta()
                yield _ndjson("delta", text=piece)
            llm_ms = (perf_counter() - llm_started) * 1000

        answer = "".join(collected)
        citation_started = perf_counter()
        citations = await asyncio.to_thread(
            extract_citations,
            answer,
            retrieval_results,
            preview_enabled=settings.CITATION_PREVIEW_ENABLED,
        )
        citation_ms = (perf_counter() - citation_started) * 1000
        trace = finalize_chat_trace(
            retrieval.trace,
            context=context,
            context_formatting_ms=context_ms,
            llm_generation_ms=llm_ms,
            followup_rewrite_ms=followup_rewrite_ms,
            followup_rewrite_status=followup_rewrite_status,
            citation_extraction_ms=citation_ms,
            time_to_first_delta_ms=(
                (first_delta_at - request_started) * 1000
                if first_delta_at is not None
                else None
            ),
            delta_window_ms=(
                (last_delta_at - first_delta_at) * 1000
                if first_delta_at is not None and last_delta_at is not None
                else None
            ),
            total_ms=(perf_counter() - request_started) * 1000,
            runtime=runtime,
            llm_called=llm_called,
        )
        response_fields = {}
        if persistent:
            response_fields = await asyncio.to_thread(
                _complete_chat_with_trace,
                request_id=request.request_id,
                standalone_question=standalone_question,
                answer=answer,
                citations=citations,
                trace=trace,
            )
        yield _ndjson(
            "citations",
            citations=[citation.model_dump(mode="json") for citation in citations],
        )
        completed = True
        yield _ndjson(
            "done",
            assistant_message_id=response_fields.get("assistant_message_id"),
            trace=_trace_dump(trace),
        )
    except (GeneratorExit, asyncio.CancelledError):
        # 方案甲：客户端断开 → 按失败处理，不保存半截答案。
        if persistent and not completed:
            try:
                await asyncio.shield(
                    asyncio.to_thread(
                        fail_chat,
                        request.request_id,
                        "interrupted",
                    )
                )
            except Exception as error:
                logger.warning(
                    "Interrupt fail_chat failed: request_id=%s error=%s",
                    request.request_id,
                    type(error).__name__,
                )
        raise
    except Exception as error:
        if persistent and not completed:
            try:
                await asyncio.to_thread(
                    fail_chat,
                    request.request_id,
                    "llm_error",
                )
            except Exception:
                pass
        yield _ndjson(
            "error",
            code="llm_error",
            message=f"LLM 调用失败: {type(error).__name__}",
        )


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request):
    """流式问答接口：NDJSON 事件流（meta → delta* → citations → done）。

    协议错误（会话不存在/冲突、非法标签、空问题）仍返回常规 HTTP 状态码；
    检索/生成期错误以 `error` 事件返回。中断采用方案甲：按失败处理。
    """
    request_started = perf_counter()
    question = request.question.strip()
    runtime = _runtime_snapshot(http_request)
    llm_called = False
    followup_rewrite_ms = None
    followup_rewrite_status = "not_applicable"
    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")
    if len(question) > settings.CONVERSATION_MESSAGE_MAX_CHARS:
        raise HTTPException(status_code=422, detail="问题超过最大长度")
    tag_filter = _normalize_tag_filter(request.tags)
    allowed_document_ids = (
        await asyncio.to_thread(resolve_document_ids, tag_filter)
        if tag_filter
        else None
    )

    persistent = request.conversation_id is not None
    started: dict = {}
    standalone_question = question
    if persistent:
        if not request.request_id:
            raise HTTPException(status_code=422, detail="持久化对话必须提供 request_id")
        try:
            started = await asyncio.to_thread(
                begin_chat,
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
        if "replay" not in started:
            history_rows = await asyncio.to_thread(
                complete_history,
                request.conversation_id,
            )
            if (
                history_rows
                and runtime.llm.enabled
                and runtime.llm.rewrite_enabled
            ):
                history_text = format_history(bounded_history(history_rows))
                rewrite_started = perf_counter()
                try:
                    llm_called = True
                    standalone_question = await asyncio.to_thread(
                        rewrite_followup,
                        question,
                        history_text,
                        runtime,
                    )
                    followup_rewrite_status = "succeeded"
                except Exception as error:
                    followup_rewrite_status = "failed"
                    logger.warning(
                        "Follow-up rewrite failed: conversation_id=%s request_id=%s error=%s",
                        request.conversation_id,
                        request.request_id,
                        type(error).__name__,
                    )
                    standalone_question = question
                finally:
                    followup_rewrite_ms = (
                        perf_counter() - rewrite_started
                    ) * 1000
            elif history_rows and runtime.llm.enabled:
                followup_rewrite_status = "disabled"

    return StreamingResponse(
        _stream_chat_events(
            request,
            started,
            standalone_question,
            tag_filter,
            allowed_document_ids,
            request_started,
            followup_rewrite_ms,
            followup_rewrite_status,
            runtime,
            llm_called,
        ),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store"},
    )
