# BM25 P6-B 生产影子索引与成套恢复验收

日期：2026-09-08。状态：通过，停在P6-C启用之前。[机器可读结果](./result-20260908-r1.json)记录摘要、参数、进程及校验结果；[专项测试](../.validation/p6b-20260908-r1.xml)为20通过。协议来源：[OPT-005 P6-B](../../../../new_docs/优化/OPT-005-BM25持久化稀疏索引.md#phase-p6b-result)。

## 生产预检与备份

本轮开始及结束均未发现其他Python服务进程，沿用停服窗口操作，没有启动或停止生产服务。实际生产库是31篇ready文档、2620个chunk，另有5条failed记录、11个标签；不是评测库的1000篇。SQLite quick_check通过，schema为4，所有chunk的parser为structured-v2，Chroma逐文档数量与登记表完全匹配，31份原文均存在。

生产参数：recursive切分，size=400、overlap=80、max_tokens=480；BM25 k1=1.5、b=0.75、epsilon=0.25；runtime revision=0，无运行时检索覆盖。现行SPARSE_BACKEND仍为rank_bm25_memory，RETRIEVAL_MODE仍为hybrid。

成套备份位于项目`data/backups/p6b-20260908-r1/backup`，包括Chroma、SQLite、原文、OCR图片、视觉缓存及环境配置。原生产Sparse目录和runtime_secrets.bin不存在，缺失状态也写入清单；没有混入评测r5/r6。总备份93,705,597字节（约89.36 MiB），磁盘空间预检通过。逐文件摘要、源路径和环境配置保留在本地忽略目录，不加入Git；公开报告只保留计数、参数和摘要。

## 影子构建

| 项目 | 实际结果 |
|---|---|
| 语料ID | production-rag-20260908 |
| generation | production-shadow-20260908-r1 |
| 位置 | `data/sparse_index/generations/production-shadow-20260908-r1` |
| 索引文件 / 整代大小 | 1,589,501 / 1,593,104字节（整代约1.52 MiB） |
| 构建 / 总流程耗时 | 6.085 / 8.228秒，单次观察值 |
| 120题K=1/5/15/50 | 四档全部120/120有序一致 |
| mmap重载最大分数差 | 0.0 |
| manifest hash | 0cf96ad3b886de2db02cd8fa7915cf0f9ed1b6a565092005d3e737b84a8f03d6 |
| 发布生产active | 否，生产active.json仍不存在 |
| 构建出站连接 / 源变化 | 0 / 无 |

通过受控CLI显式匹配真实生产路径并填写确认值，从停写源的工作快照构建，没有复用评测generation。构建后完整文件hash、manifest、chunk ID/正文/metadata摘要及mmap均已验证。

## 恢复与回滚

原始备份复制到本轮`restore`目录，所有资产摘要先逐一匹配，再仅在恢复目录发布影子代。服务验证使用真实应用的ASGI startup/shutdown和HTTP接口，由三个独立Python进程执行；并非生产端口或浏览器验收。为避免外部调用，关闭模型预热、LLM与OCR；没有运行Dense/Reranker完整推理。

| 进程 | 后端 | 120题四档排序校验 | 标签范围 | 4线程并发 | 句柄释放 |
|---|---|---:|---:|---:|---|
| boot1，PID 79572 | bm25s_mmap | 480/480 | 11 | 24/24 | 通过 |
| boot2，PID 62196 | bm25s_mmap | 480/480 | 11 | 24/24 | 通过 |
| rollback，PID 40136 | rank_bm25_memory | 480/480 | 11 | 24/24 | 通过 |

每次运行都在同一恢复语料上与旧后端比较有序结果，三个进程的查询签名相同。boot1还在恢复目录构建另一候选代，完成切换、480项排序对照、切回及再次对照。11个标签覆盖实际父子范围解析、精确子语料检索和文档列表HTTP接口，空集合短路也通过。回滚以新进程显式选择旧backend验证，而非静默降级。

首次服务探针错误复用了构建CLI的“禁止一切IP连接”规则，拦截Windows asyncio内部的loopback socketpair；该次未完成服务启动。修正验收工具为仅允许本机回环、仍拒绝外部网络后，三个进程全部通过，外部连接次数均为0。这是验收工具修正，未放宽索引正确性门禁。

## 最终边界与下一步

最终再次核对生产Chroma、SQLite、原文/OCR/缓存、环境配置和原始备份：摘要全部未变化；影子manifest与构建结果匹配。生产默认仍是旧BM25，活动指针仍不存在，服务保持开始时的停止状态。新辅助工具及受控CLI专项20项通过；本轮未修改业务检索代码，没有重跑P6-A全量802项/前端39项，保留其原有验收日期。

本轮固定120题验证的是新旧BM25在真实生产语料上的计算一致性，不是把评测gold标签应用到这31篇文档计算召回率。没有重跑1000篇完整GPU性能基准或1万篇扩展。P6-C仍需重新核对源摘要和配置、正式发布active及默认配置、完整检索回归和观察窗口；若期间生产数据有变化，不能直接启用本影子代。

工具：[verify_sparse_shadow.py](../../../../scripts/verify_sparse_shadow.py)。阶段命令为backup → inspect → 受控rebuild_sparse_index（无publish）→ prepare-restore → service boot1/boot2/rollback → finalize。报告/备份拒绝覆盖，恢复服务只接受本轮restore内路径。
