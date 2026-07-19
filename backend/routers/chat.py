"""
问答接口 — POST /api/chat

接收用户问题，走完完整问答链路：
问题向量化 → 语义检索 → Prompt 拼接 → LLM 生成 → 引用提取

路由层保持轻薄：只做参数校验和调用 services。
"""

from time import perf_counter

from fastapi import APIRouter, HTTPException
from backend.models.schemas import ChatRequest, ChatResponse
from backend.services.retriever import (
    retrieve_with_trace,
    format_retrieval_context,
)
from backend.services.retrieval_trace import finalize_chat_trace
from backend.services.llm import ask
from backend.utils.citation import extract_citations

router = APIRouter(tags=["chat"])


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

    # 1. 语义检索
    retrieval = retrieve_with_trace(question)
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
        return ChatResponse(
            answer="未找到与您问题相关的文档内容。请确认已上传相关文档，或尝试换个问法。",
            citations=[],
            trace=trace,
        )

    # 2. 格式化上下文
    context_started = perf_counter()
    context = format_retrieval_context(retrieval_results)
    context_ms = (perf_counter() - context_started) * 1000

    # 3. 调用 LLM 生成答案
    llm_started = perf_counter()
    try:
        answer = ask(question, context)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM 调用失败: {str(e)}"
        )
    llm_ms = (perf_counter() - llm_started) * 1000

    # 4. 提取引用来源
    citations = extract_citations(answer, retrieval_results)
    trace = finalize_chat_trace(
        retrieval.trace,
        context=context,
        context_formatting_ms=context_ms,
        llm_generation_ms=llm_ms,
        total_ms=(perf_counter() - total_started) * 1000,
    )

    return ChatResponse(
        answer=answer,
        citations=citations,
        trace=trace,
    )
