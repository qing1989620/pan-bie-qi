# 判定方法、数据与实测比较

## 判定依据

1. **声明与配置。** 解析 `SKILL.md` 的元数据、明确的环境变量要求、适用场景和限制。源码中疑似引用的依赖记为线索。
2. **可核对的源码问题。** 分析可读取的 Python 语法、文档入口和受支持的字面参数声明。通过只表示当前核验项目相符。报告提供文件、行号、片段和判定理由。
3. **已测效果。** 对相同任务、相同模型分别比较不读和读入说明时的产物。把两组通过率相减得到增益，保留改善和回退。仅为精确版本附上这类记录。

## 本地案例

真实来源为固定提交的 [工位标签归一化 Skill](https://github.com/benchflow-ai/skillsbench/blob/27738384b1df694ea2ae466e416f476e94d8fab9/tasks/manufacturing-codebook-normalization/environment/skills/manufacturing-failure-reason-codebook-normalization/SKILL.md)。测试使用该说明、32 组合成工位与编码输入和文本模型输出。每个输入有同模型的两个条件，共保存 64 份产物及登记答案。

程序重新枚举匹配工位与完整别名的候选。恰好一个候选时输出其编码，零个或多个时应为 `UNKNOWN`。规则验收在这批产物上判对 64/64，Flash 直接评审判对 39/64 并误放行 23 份。去掉工位条件后误拒 15 份，放宽多匹配和跳过无匹配分别误放行 16 份。前述准确率对应一个契约上的已有产物。

全量配对中，无 Skill 通过 15/32，有 Skill 通过 16/32。留出均为 8/16。同工位多码与无合法匹配各有 8 项，两个条件均未通过。这表明已测 Skill 在这些边界输入中仍需要更可靠的实施。

复核材料包括 [输入与登记答案](../src/skill_discriminator/data/codebook-cases.json)、[实际配对产物](../src/skill_discriminator/data/codebook-outcomes.json)和[规则消融](../src/skill_discriminator/data/codebook-ablation.json)。运行 `python -m skill_discriminator.verify`，或在网页点击「复核 64 份产物」。程序检查档案指纹并重新验收实际产物。本次复核使用已保存输出。

## 公开强基线对照

在 [固定 SkillPortrait 发布版](https://github.com/SkillAudit/skillaudit/tree/349eff1e12cf6f210aa9b865a9e865b948b5a89b/docs/artifacts/data)上复算了 1,277 个技能环境分。逐条记录显示，279 个场景的负增益在该发布档案中为零。在 1,185 个有共同记录的组合上，所有方法使用两项试跑判断第三项任务。

| 方法 | 前排命中改善（474 项） | 复查找到回退（237 项） | 增益平均误差 |
| --- | ---: | ---: | ---: |
| SkillAudit 发布增益 | 319 | 18 | 23.15 个百分点 |
| SkillTester 公开公式移植 | 285 | 30 | 不适用 |
| 逐条复算并保留正负增益 | 318 | 29 | 24.73 个百分点 |

这些结果支持保留已经测到的回退。未来任务的增益点预测仍有较大误差。扩展特征在 SkillsBench 公开任务验收记录上没有改善预测，消融结果与区间见 [中文双栏论文](skill-discriminator-paper.pdf)以及[汇总数据](../src/skill_discriminator/data/utility-study.json)。论文区分了已生成产物、公开档案复算、公式移植和应用覆盖。
