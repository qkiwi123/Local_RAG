# V3-5隔离调参工作区

创建：2026-08-29；更新：2026-08-31。这里保存调参实验数据，不连接生产库，不覆盖
`../runs/gpu-baseline-120q-v31-k15-20260828/`封存基线。

## A阶段结果

- 实验：`v35-a-baseline-20260829/`。
- 协议：`protocol.json`；索引副本：`storage/`；A验收：`phase-a-acceptance.json`；最终代码/测试哈希：`phase-a-final-verification.json`。
- 72道development题完成真实GPU检索；60/60道可回答题最终顺序与历史基线一致，质量指标完全一致。
- 2/12道不可回答题从Dense阶段出现ANN候选差异；它们没有gold，不改变本轮可回答质量指标。后续V3-5以本复制库的control结果为参数比较基准。
- 初版严格验收把上述两道差异也判为失败，原`summary.json`保留；修订口径写入新的`phase-a-acceptance.json`，没有覆盖失败证据。
- 生成模型未调用；Embedding和Reranker均在`cuda:0`完成实际推理；无出站IP连接。

## 命令

```powershell
.\.venv-gpu\Scripts\python.exe -B -u eval\crud_rag_1000\v3_1\tuning_runner.py prepare --experiment-id <唯一ID>
.\.venv-gpu\Scripts\python.exe -B -u eval\crud_rag_1000\v3_1\tuning_runner.py register-config --experiment-id <唯一ID> --config-json <候选配置.json>
.\.venv-gpu\Scripts\python.exe -B -u eval\crud_rag_1000\v3_1\tuning_runner.py run --experiment-id <唯一ID> --split development
.\.venv-gpu\Scripts\python.exe -B -u eval\crud_rag_1000\v3_1\tuning_runner.py run --experiment-id <唯一ID> --split development --resume
.\.venv-gpu\Scripts\python.exe -B -u eval\crud_rag_1000\v3_1\tuning_runner.py accept-a --experiment-id <唯一ID>
```

当前缓存仅支持完全相同配置的逐题断点续算。跨配置的query向量、候选或pair score缓存尚未证明等价，因此保持关闭；缓存重放耗时不能作为检索性能。

Chroma首次打开副本会更新`chroma.sqlite3`中的`acquire_write`运行簿记。prepare仍要求源与副本逐字节一致；运行后要求HNSW文件和metadata.db哈希不变，并要求SQLite quick_check、1000文档、2914 embeddings等逻辑契约不变。

## 后续阶段状态

- B：六配置模式消融已完成。
- C：Dense K/距离扫描已完成，保留K15/0.67。
- D：选择Dense20/Reranker20，并保留Top10/配额2质量候选和Top5/配额2成本候选。
- B2：Hybrid BM25/RRF补充扫描已完成；最佳配置只与Dense平局，未推广。
- E：`phase-e/v35-e-evidence-gate-guard-20260831/`已完成16策略离线重放，M2为0/60误拒、11/12拦截、0扰动翻转；145项回归及最终验收通过。
- F：CPU版`.venv`预热失败目录保留；有效`phase-f/v35-f-validation-m2-gpu-20260831/`以`.venv-gpu`完成72次正式检索。M2为1/20误拒、1/4拦截，Top10/Top5均不低于原始基线门槛失败，结论`not_adopted`；164项回归和最终执行验收通过。
- G、S7及生产默认值变更均未执行。

E/F入口分别为`run_phase_e_evidence_gate.py`和`run_phase_f_validation.py`，均以`run`执行、`verify`结合JUnit做最终只读验收。E/F产物不代表真实LLM拒答；F单轮描述耗时也不替代G性能复测。
