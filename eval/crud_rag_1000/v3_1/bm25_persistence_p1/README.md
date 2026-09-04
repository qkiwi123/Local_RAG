# BM25 持久化 P1 隔离实验

本目录只承载 OPT-005 P1 证据，不是业务实现。P1 未修改正式依赖、生产配置或生产数据。

## 固定输入

- 语料：`../tuning/v35-a-baseline-20260829/storage`，1000篇、2914 chunks
- 题库：`../questions_v3_120q.json`，120题
- 当前oracle：`rank_bm25.BM25Okapi(k1=1.5, b=0.75, epsilon=0.25)`
- 候选：`bm25s==0.3.11`，仅安装到本目录`_deps`

隔离依赖安装命令：

```powershell
.\.venv\Scripts\python.exe -m pip install --target eval\crud_rag_1000\v3_1\bm25_persistence_p1\_deps --no-deps bm25s==0.3.11
```

## 结果入口

- 排序/mmap：[报告](./runs/equivalence-20260903-r2/report.md)与[机器结果](./runs/equivalence-20260903-r2/result.json)
- 优化前系统基线：[修正版报告](./runs/old-baseline-20260903/report-v2.md)与[修正版汇总](./runs/old-baseline-20260903/summary-v2.json)

原始`summary.json`的`answerable_count`因汇总字段名差异为`null`，文件为保留原始证据未覆盖；`summary-v2.json`修正为100并记录六份原始文件SHA-256。每轮Chroma工作副本已在报告生成后删除以节省约122 MiB，可由固定源重新复制；逐题JSONL、结果、索引与指纹证据保留。

## 已确认边界

原生BM25S配置不等价。只有在ATIRE TF上显式复现当前`rank_bm25`的epsilon负IDF下限，120题K=1/5/15/50才全部有序一致。该实验结论只允许候选进入P2，不代表业务接入、系统收益或生产切换已经完成。
