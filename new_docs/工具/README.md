# 文档工具导航

创建：2026-08-27；更新：2026-09-08。这里不是业务运行工具，不导入backend，不使用模型。保留[P6-A检查](./validation-report-20260908-bm25-p6a.json)及[P6-B检查](./validation-report-20260908-bm25-p6b.json)独立报告。

| 文件 | 用途 |
|---|---|
| [check_docs.py](./check_docs.py) | 标准库静态检查；默认只读 |
| [source_snapshot.json](./source_snapshot.json) | 整理前1427个原文件hash及Git基准，固定历史证据 |
| [validation-report.json](./validation-report.json) | 2026-08-27首次整理验收结果，保留历史证据 |
| [validation-report-20260828-v35-plan.json](./validation-report-20260828-v35-plan.json) | 2026-08-28 V3-5方案日常维护静态检查，不含业务复测 |
| [validation-report-20260829-v35-a.json](./validation-report-20260829-v35-a.json) | 2026-08-29 V3-5 A实现、Dense差异诊断与验收文档同步后的静态检查 |
| [validation-report-20260829-v35-b.json](./validation-report-20260829-v35-b.json) | 2026-08-29 V3-5 B模式消融实现、结果与阶段边界同步后的静态检查 |
| [validation-report-20260829-v35-c.json](./validation-report-20260829-v35-c.json) | 2026-08-29 V3-5 C Dense参数扫描、结果与阶段边界同步后的静态检查 |
| [validation-report-20260830-v35-d.json](./validation-report-20260830-v35-d.json) | 2026-08-30 V3-5 D重排/上下文预算、结果与阶段边界同步后的最新静态检查 |
| [validation-report-20260831-v35-b2.json](./validation-report-20260831-v35-b2.json) | 2026-08-31 V3-5补充B2 Hybrid BM25/RRF扫描、结果与阶段边界同步后的最新静态检查 |
| [validation-report-20260831-v35-e.json](./validation-report-20260831-v35-e.json) | 2026-08-31 V3-5 E离线门控、M2选择、结果与阶段边界同步后的最新静态检查 |
| [validation-report-20260831-v35-f.json](./validation-report-20260831-v35-f.json) | 2026-08-31 V3-5 F固定validation验证、不采用结论及阶段边界同步后的最新静态检查 |
| [validation-report-20260903-bm25-persistence-plan.json](./validation-report-20260903-bm25-persistence-plan.json) | 2026-09-03 BM25持久化实施方案确认前的静态检查 |
| [validation-report-20260903-bm25-p1.json](./validation-report-20260903-bm25-p1.json) | 2026-09-03 BM25持久化P1结果同步后的静态检查 |
| [validation-report-20260903-bm25-p2.json](./validation-report-20260903-bm25-p2.json) | 2026-09-03 BM25持久化P2实现、构建及回归结果同步后的静态检查 |
| [validation-report-20260903-bm25-p3.json](./validation-report-20260903-bm25-p3.json) | 2026-09-03 BM25持久化P3业务接入、回放及回归结果同步后的静态检查 |
| [validation-report-20260903-bm25-p4.json](./validation-report-20260903-bm25-p4.json) | 2026-09-03 BM25持久化P4同期质量、性能、资源及阶段边界同步后的静态检查 |
| [validation-report-20260903-bm25-p5-p6-plan.json](./validation-report-20260903-bm25-p5-p6-plan.json) | 2026-09-03 BM25 P5条件采用与P6生产实施协议确认前的静态检查 |

仓库根日常维护命令：

```powershell
.\.venv\Scripts\python.exe new_docs\工具\check_docs.py
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260828-v35-plan.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260829-v35-a.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260829-v35-b.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260829-v35-c.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260830-v35-d.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260831-v35-b2.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260831-v35-e.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260831-v35-f.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-persistence-plan.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p1.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p2.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p3.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p4.json
.\.venv\Scripts\python.exe -B new_docs\工具\check_docs.py --self-test --report new_docs\工具\validation-report-20260903-bm25-p5-p6-plan.json
```

2026-08-27首次整理曾执行的命令（不作为后续代码变化后的日常检查要求）：

```powershell
.\.venv\Scripts\python.exe new_docs\工具\check_docs.py --check-preserved --self-test --report new_docs\工具\validation-report.json
```

--check-preserved仅用于首次整理；后续真实代码变化无需永久通过旧快照，不覆盖旧报告或重置旧快照。--report仅允许写入本目录。--self-test在内存构造错误，不改原文档。

## 关联文档

- 关系：实现；[DEV-013](../开发/DEV-013-文档检查工具.md)。
- 关系：验收；[TEST-008](../测试/TEST-008-文档完整性验收.md)。
- 关系：维护；[OPS-004](../运维/OPS-004-文档维护与变更流程.md)。
- 关系：总导航；[文档中心](../README.md)。
