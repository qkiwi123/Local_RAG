# BM25 持久化 P2 结果

P2已实现持久化索引模块、不可变generation、manifest校验、Windows mmap加载、活动指针及隔离构建命令；尚未接入现有Hybrid查询，业务仍使用`rank_bm25`。

## 最终隔离构建

- 活动generation：`p2-1000-20260903-r5`
- 待机/复现generation：`p2-1000-20260903-r6`
- 输入：1000篇、2914 chunks、120题
- 每个generation索引文件：4,261,289字节，不含正文
- r5/r6六个索引文件的大小和SHA-256逐项一致
- 两次oracle：K=1/5/15/50均为120/120有序一致，最大分数差0
- r5总构建命令wall：8.004128秒；r6为7.283266秒
- 源Chroma/SQLite前后指纹一致；出站网络事件0；临时Chroma快照残留0

证据入口：

- [r5构建结果](./build-result-r5.json)
- [r6复现结果](./build-result-r6.json)
- [活动指针](./sparse-index/active.json)
- [r5 manifest](./sparse-index/generations/p2-1000-20260903-r5/manifest.json)
- [r6 manifest](./sparse-index/generations/p2-1000-20260903-r6/manifest.json)

## 失败与修正记录

1. 首次正式命令在加载项目模块前退出，原因是脚本入口未加入项目根目录；未进入索引构建。
2. 首次加入generation级oracle时，TopK全部一致但分数差约`3.78e-12`。原因是独立IDF累计顺序与`rank_bm25`不同；没有放宽门禁，改为直接使用oracle的`average_idf`后达到分数差0。
3. 两次相同输入的早期索引文件hash不同，原因是BM25S字符串入口以set生成词表；改为Unicode词项排序和显式token-ID入口后，r5/r6文件逐字节一致。
4. 早期Windows快照清理被第二个Chroma客户端句柄阻塞；分词器已抽为无存储副作用模块，CLI现在只打开一个显式客户端并在清理前停止，清理失败不再忽略。

早期generation、失败临时目录和被替代报告均已清理；r5作为活动隔离generation，r6保留用于跨进程复现及回滚验证。P3开始前不得把该活动指针配置到业务检索。

## 验证

- P2/Sparse相关：55项通过。
- 全量Python回归：742项通过、3项跳过、7个既有依赖弃用警告。
- 第一次全量回归因既有测试要求`tmp_path`位于v3.1目录而出现1个环境失败；按该合同指定`--basetemp`后全套通过，未修改测试断言。
- `.venv`和`.venv-gpu`依赖检查均通过。
