"""Streamlit 检索 Trace 视图：阶段表、完整 chunk 与耗时瓶颈。"""

import streamlit as st


STAGE_LABELS = {
    "dense": "Dense",
    "bm25": "BM25",
    "rrf": "RRF",
    "reranker": "Reranker",
    "selected": "最终上下文",
}

TIMING_LABELS = {
    "query_embedding": "查询向量化",
    "dense_retrieval": "Dense 检索",
    "sparse_retrieval": "Sparse 检索",
    "rrf": "RRF 融合",
    "rerank": "Reranker 精排",
    "context_formatting": "上下文组装",
    "llm_generation": "LLM 生成",
}

STAGE_COLUMNS = {
    "dense": [
        "排名", "文档", "页码", "chunk_id", "distance", "过滤状态",
        "是否入选", "摘要",
    ],
    "bm25": [
        "排名", "文档", "页码", "chunk_id", "BM25", "是否入选", "摘要",
    ],
    "rrf": [
        "排名", "文档", "页码", "chunk_id", "Dense名次", "BM25名次",
        "RRF", "是否入选", "摘要",
    ],
    "reranker": [
        "排名", "文档", "页码", "chunk_id", "Reranker", "是否入选", "摘要",
    ],
    "selected": [
        "排名", "文档", "页码", "chunk_id", "distance", "BM25", "RRF",
        "Reranker", "是否入选", "摘要",
    ],
}


def executed_stage_names(trace: dict) -> list[str]:
    """按后端执行顺序返回前端支持的阶段。"""
    return [
        stage["name"]
        for stage in trace.get("stages", [])
        if stage.get("name") in STAGE_LABELS
    ]


def candidate_rows(trace: dict, stage_name: str) -> list[dict]:
    """将阶段候选和去重 chunk 目录连接成表格行。"""
    chunks = trace.get("chunks", {})
    stage = next(
        (
            item
            for item in trace.get("stages", [])
            if item.get("name") == stage_name
        ),
        {"candidates": []},
    )
    rows = []
    for candidate in stage.get("candidates", []):
        chunk = chunks.get(candidate.get("chunk_id"), {})
        content = chunk.get("content", "")
        preview = (
            content
            if len(content) <= 120
            else content[:120].rstrip() + "…"
        )
        rows.append(
            {
                "排名": candidate.get("rank"),
                "文档": chunk.get("document_name", "unknown"),
                "页码": chunk.get("page_number", 0),
                "chunk_id": candidate.get("chunk_id", ""),
                "distance": candidate.get("distance"),
                "Dense名次": candidate.get("dense_rank"),
                "BM25名次": candidate.get("bm25_rank"),
                "BM25": candidate.get("bm25_score"),
                "RRF": candidate.get("rrf_score"),
                "Reranker": candidate.get("reranker_score"),
                "过滤状态": candidate.get("filtered_reason") or "通过",
                "是否入选": "是" if candidate.get("selected") else "否",
                "摘要": preview,
            }
        )
    return rows


def remember_candidate_selection(
    message_key: str,
    stage_name: str,
    selected_rows: list[int],
    session_state,
) -> bool:
    """记录真正发生变化的表格选择；返回是否需要额外重跑一次。"""
    selected_rows = list(selected_rows or [])
    snapshot_key = f"_trace_selection_{message_key}_{stage_name}"
    active_key = f"_trace_active_selection_{message_key}"
    previous_rows = session_state.get(snapshot_key, [])
    session_state[snapshot_key] = selected_rows
    if selected_rows == previous_rows:
        return False

    current = session_state.get(active_key)
    if selected_rows:
        selected = {
            "stage_name": stage_name,
            "row_index": selected_rows[0],
        }
        if current == selected:
            return False
        session_state[active_key] = selected
        return True
    if current and current.get("stage_name") == stage_name:
        session_state.pop(active_key, None)
        return True
    return False


def selected_candidate_from_state(
    trace: dict,
    message_key: str,
    session_state,
) -> dict | None:
    """在创建详情面板前恢复所选候选，供表格重跑后继续展示。"""
    stage_names = executed_stage_names(trace)
    selected = session_state.get(
        f"_trace_active_selection_{message_key}",
    )
    if not selected:
        return None
    active_stage = selected.get("stage_name")
    if active_stage not in stage_names:
        return None
    rows = candidate_rows(trace, active_stage)
    selected_index = selected.get("row_index", -1)
    if selected_index < 0 or selected_index >= len(rows):
        return None
    return {"stage_name": active_stage, "row": rows[selected_index]}


def _mode_label(trace: dict) -> str:
    mode = trace.get("retrieval_mode", "unknown").title()
    if trace.get("reranker_enabled"):
        mode += " + Reranker"
    return mode


def _render_full_chunk(trace: dict, selected: dict) -> None:
    chunk = trace.get("chunks", {}).get(selected["chunk_id"])
    if not chunk:
        st.warning("该候选缺少 chunk 原文，无法展开。")
        return
    with st.container(border=True):
        st.caption(
            f"完整 Chunk · {chunk['document_name']} · "
            f"第 {chunk['page_number']} 页 · {selected['chunk_id']}"
        )
        st.markdown(chunk.get("content", ""))


def _render_timings(timings: dict) -> None:
    measured = {
        key: float(timings.get(key) or 0)
        for key in TIMING_LABELS
        if float(timings.get(key) or 0) > 0
    }
    if not measured:
        return
    st.markdown("**阶段耗时**")
    maximum = max(measured.values())
    for key, milliseconds in measured.items():
        st.progress(
            milliseconds / maximum,
            text=f"{TIMING_LABELS[key]} · {milliseconds:.1f} ms",
        )


def render_retrieval_trace(trace: dict | None, message_key: str) -> None:
    """渲染默认折叠、可按阶段选择候选行的 Trace 面板。"""
    if not trace:
        return

    context = trace.get("context", {})
    timings = trace.get("timings_ms", {})
    label = (
        f"🔎 检索详情 · {_mode_label(trace)} · "
        f"{context.get('chunk_count', 0)} chunks · "
        f"{float(timings.get('total') or 0) / 1000:.2f}s"
    )
    selected_candidate = selected_candidate_from_state(
        trace,
        message_key,
        st.session_state,
    )
    open_key = f"_trace_open_{message_key}"
    is_open = bool(st.session_state.get(open_key, False))
    action = "🔽 收起" if is_open else "▶️ 展开"
    if st.button(
        f"{action} · {label}",
        key=f"trace_toggle_{message_key}",
        use_container_width=True,
    ):
        st.session_state[open_key] = not is_open
        st.rerun()
    if not st.session_state.get(open_key, False):
        return

    with st.container(border=True):
        summary = st.columns(3)
        summary[0].metric("候选阶段", len(executed_stage_names(trace)))
        summary[1].metric(
            "最终上下文",
            f"{context.get('chunk_count', 0)} chunks",
            f"{context.get('token_count', 0)} tokens",
        )
        summary[2].metric(
            "总耗时",
            f"{float(timings.get('total') or 0):.1f} ms",
        )

        if selected_candidate:
            st.caption(
                f"当前选择 · {STAGE_LABELS[selected_candidate['stage_name']]}"
            )
            _render_full_chunk(trace, selected_candidate["row"])

        stage_names = executed_stage_names(trace)
        if not stage_names:
            st.info("本次响应没有可展示的检索阶段。")
            _render_timings(timings)
            return

        tabs = st.tabs([STAGE_LABELS[name] for name in stage_names])
        for stage_name, tab in zip(stage_names, tabs):
            with tab:
                rows = candidate_rows(trace, stage_name)
                if not rows:
                    st.caption("该阶段没有候选。")
                    continue
                event = st.dataframe(
                    rows,
                    column_order=STAGE_COLUMNS[stage_name],
                    hide_index=True,
                    width="stretch",
                    on_select="rerun",
                    selection_mode="single-row",
                    key=f"trace_table_{message_key}_{stage_name}",
                )
                if remember_candidate_selection(
                    message_key,
                    stage_name,
                    event.selection.rows,
                    st.session_state,
                ):
                    st.rerun()

        _render_timings(timings)
