# 评测指标表格统一设计

## 目标

统一评测报告与 `eval/README.md` 的指标口径，确保当前 11 项汇总指标全部展示，并让后续生成的 `report.md` 自动包含“指标含义”列。

## 设计

1. 将 `eval/report.py` 中的指标元数据扩展为单一有序定义，包含指标键、展示名称和中文含义。
2. `write_report_md()` 使用该定义生成三列表格：`指标 | 指标含义 | 值`。
3. `render_compare()` 继续复用同一指标定义，保持现有对比表结构不变。
4. `eval/README.md` 的“指标解读”补齐全部 11 项，名称和含义与生成器一致，并保留 baseline 参考值。
5. 回写现有 `eval/runs/baseline/report.md` 和本地 `eval/runs/p0-1-citation-source-id/report.md` 的汇总指标表；不重新运行评测，不改变历史指标值。

## 11 项指标

- `hit_rate`
- `recall`
- `mrr`
- `keypoint_coverage`
- `strict_accuracy`
- `citation_precision`
- `citation_recall`
- `citation_emission_rate`
- `refusal_accuracy`
- `false_refusal_rate`
- `unanswerable_citation_fp`

## 验证

- 添加报告生成单元测试，断言表头包含“指标含义”、11 项指标及对应说明。
- 断言对比报告仍能正常生成，避免指标元数据结构调整造成回归。
- 检查 README 和两份现有报告均包含 11 项指标。
- 运行相关单元测试及完整测试集。
