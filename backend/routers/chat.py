"""
问答接口 — POST /api/chat

接收用户问题，走完完整问答链路：
问题向量化 → 语义检索 → Prompt 拼接 → LLM 生成 → 引用提取

路由层保持轻薄：只做参数校验和调用 services。
"""

from fastapi import APIRouter, HTTPException
from backend.models.schemas import ChatRequest, ChatResponse
from backend.services.retriever import retrieve, format_retrieval_context
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
    question = request.question.strip()

    if not question:
        raise HTTPException(status_code=422, detail="问题不能为空")

    # 1. 语义检索
    retrieval_results = retrieve(question)

    if not retrieval_results:
        # 没有检索到任何相关内容
        return ChatResponse(
            answer="未找到与您问题相关的文档内容。请确认已上传相关文档，或尝试换个问法。",
            citations=[],
        )

    # 2. 格式化上下文
    context = format_retrieval_context(retrieval_results)

    # 3. 调用 LLM 生成答案
    try:
        answer = ask(question, context)
    except Exception as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM 调用失败: {str(e)}"
        )

    # 4. 提取引用来源
    citations = extract_citations(answer, retrieval_results)

    return ChatResponse(
        answer=answer,
        citations=citations,
    )
