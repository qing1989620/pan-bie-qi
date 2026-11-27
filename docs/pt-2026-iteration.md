# PT 1.7.0 方法复现与框架迭代

PT 接收用户任务和目标环境，从公开目录收集 Skill，用单链接判别器核对固定版本，再提供附理由的推荐与替代项。12 条开发和回归任务中，首条文档适用性由 5/12 提升到 12/12。针对第一组迁移任务的失败修复后达到 1/4。第二组四条任务在调整完成后冻结，首条符合从 1/4 增至 2/4。

## 2026 年方法与复现范围

| 模块 | 参照工作 | 本轮执行与改动 |
| --- | --- | --- |
| 检索 | [SkillFlow，COLM 2026](https://proceedings.colm.cc/paper_files/paper/2026/hash/425ee25d6c22ef98b67328273b8f95d5-Abstract-Conference.html)、[R3-Skill v3，预印本](https://arxiv.org/html/2606.03565v3) | 采用分级检索的思路。给指定工具保留操作词，必要时并行给出短查询。R3 对照使用作者冻结的 5,696 条查询、2,050 个 Skill 和原始指标脚本。 |
| 判别 | [OpenSkillEval，EMNLP 2026 Findings](https://aclanthology.org/2026.findings-emnlp.1554/)、[SkillsBench 发布档案](https://huggingface.co/datasets/benchflow/skillsbench-leaderboard/blob/f104580363a9642563593c475620196ecd36687d/analysis/experiment_final_selected_manifest.json) | 从公开记录复算同一任务、模型及宿主的有无 Skill 配对。产品复用既有判别器，核对工具、行为及产物，历史评测保留原始适用范围。 |
| 推荐 | [Graph-of-Skills，EMNLP 2026 主会](https://aclanthology.org/2026.emnlp-main.2614/)、[STAR，IEEE Transactions on Services Computing 2026](https://doi.org/10.1109/TSC.2026.3704136) | 运行 Graph-of-Skills 作者的小图测试。借鉴剩余需求思想，在小候选池中穷举最大覆盖集合。对照固定候选数和推荐数，比较旧贪心与新选法。 |

论文级复现以版本固定、指标口径一致为前提。目前各对照的协议分别是冻结语料检索、公开执行记录再汇总、合成覆盖实验和真实目录回放。四类指标的分母不同。完整 SkillFlow、OpenSkillEval、Graph-of-Skills 和 STAR 端到端实验尚未在 PT 上复跑。

Graph-of-Skills 的三个原始测试通过，覆盖正向传播、反向一步补齐前置依赖和固定输出预算的无图对照。作者发布的工作区是旧版无向结构，新版有向依赖消融需要重建工作区，论文的依赖对召回数仍为外部参照。[固定测试代码](https://github.com/davidliuk/graph-of-skills/blob/203f60a2c689da055ce1ac351eb3cb9912a3bca7/tests/test_skillsbench_graphskills.py) · [工作区说明](https://github.com/davidliuk/graph-of-skills/blob/203f60a2c689da055ce1ac351eb3cb9912a3bca7/DATA.md)

## 复现 R3 官方权重与重排试验

在固定提交的完整测试集合上运行 Tencent 公开的 R3-Embedding-0.6B。使用作者的查询指令、4,096 token 上限、末 token 池化、L2 归一化及指标脚本。本地 RTX 4050 以 FP16 推理，权重、语料和评分脚本核对 SHA-256。四项核心指标与作者 v3 的差值绝对值均在 0.03 个百分点以内。[论文 v3 与公开权重](https://arxiv.org/html/2606.03565v3) · [本地复算结果](results/pt-r3-v3-reproduced.json)

| 同 5,696 条查询 | Hit@1 | nDCG@10 | Recall@10 | Set-Compat |
| --- | ---: | ---: | ---: | ---: |
| R3-Embedding v3 作者值 | 72.07% | 81.60% | 91.77% | 28.12% |
| R3-Embedding v3 权重本地复现 | 72.09% | 81.58% | 91.75% | 28.12% |
| R3-Reranker v3 作者值 | 75.21% | 81.73% | 89.69% | 31.88% |

用独立于标签的哈希规则抽取 32 条查询，对相同前 20 项执行官方重排权重，共 640 个配对输入。使用原始 yes/no 打分标签，先裁剪文档以保留提示末尾。该样本中 Hit@1 从 68.75% 升至 71.88%，nDCG@10 从 79.85% 降至 73.92%，Recall@10 从 92.19% 降至 79.69%。此表外抽样不能替代全量重排对比，现有在线推荐未引入该重排器。[重排抽样](results/pt-r3-v3-rerank-sample.json)

检验在 R3 前 20 个候选重排时加入标题与简介 BM25 是否还能提高效果。将同一正确 Skill、同文档和共同出现的正例合并分组，4,376 条用于选权重，1,320 条用于留出比较。固定权重方案选出 0。只在 R3 前两名相近时使用词法分的方案，在留出集把 nDCG@10 从 79.66% 降至 79.61%，差值区间为 −0.151 至 +0.048 个百分点，Recall@10 保持 90.66%。这两种融合保留为实验结果，产品采用通过真实目录回放的增强。[留出重排数据](results/pt-r3-v3-fusion.json)

## 真实目录对照

系统先真实查询 skills.sh、读取 GitHub 固定提交，再对同一份目录和来源捕获回放三个版本。旧版为提交 420f191。中间版本保持旧查询，启用新版判别和推荐。最终版本使用 1.7.0 的完整路径。各版本最多核查十个候选、返回三条。所有请求关闭模型。

人工逐条核对首项文档中的工具、主要操作和关键产物，分为符合、部分符合、错配、弃权。一次复核由同一位评审完成。12 条开发项在调整中已有回看，第一组四条迁移也用于诊断。代码冻结后另跑第二组四条任务。复核记录附上固定提交、文档指纹、依据行和判断理由。[逐条记录](results/pt-v17-field-comparison.json) · [第二组迁移](results/pt-v17-second-transfer.json)

| 任务 | 1.6.1 | 旧查询＋新版核查 | 1.7.0 |
| --- | --- | --- | --- |
| 六条早期回归 | 4/6 | 4/6 | 6/6 |
| 六条开发期新任务 | 1/6 | 4/6 | 6/6 |
| 第一组迁移，含诊断后重测 | 0/4 | 0/4 | 1/4 |
| 第二组迁移，调规则后冻结 | 1/4 | 未测 | 2/4 |
| 首条明确错配数，前 16 条 | 7 | 0 | 0 |
| 首条部分符合数，前 16 条 | 3 | 2 | 2 |
| 弃权数，前 16 条 | 1 | 6 | 1 |

只换核查规则，旧查询找到的 PlantUML、Plotly 与 Playwright 合适候选可以提到首位。Matplotlib、Mermaid、JSON Schema 和 Docker Compose 仍缺可判的来源。使用新版查询后，这四个任务也出现了带产物或步骤依据的首选。曾排入备选的 Mermaid 转换器会把现成 Mermaid 转成 HTML/SVG。增加产出方向规则后，它被筛除。

第一组的 Rust 首项曾推荐 Evolve SDK 专用 Skill。增加 SDK 适用范围核查后，通用 parser 测试 Skill 提到首位，在线 API 复跑耗时 19.66 秒。同组的 Altair 返回空集合，DuckDB 缺少本地 Parquet 工作流，FFmpeg 缺字幕依据。第二组仍有 Pandoc 的转换方向错配和 Go CI 缺少 JUnit 产物的情况。Mermaid 的单次在线请求为 38.23 秒。样本有限，回放计时独立于线上延迟。

## 配对实测与组合选择

从 SkillsBench 的 1,150 条发布记录筛出无超时的有、无 Skill 双臂，分别在同任务、模型、宿主内取均值。180 个匹配单元覆盖 87 个任务。平均奖励由 35.58% 提至 50.64%，差值为 15.06 个百分点，按任务聚类的 2,000 次重采样区间为 9.30 至 20.94 个百分点。64 个单元上升，24 个下降，92 个持平。

在 Gemini 与 MiniMax 共同覆盖的 86 个任务上，把一个模型里的正收益当成另一模型的采用条件，两个方向分别有 4/26 与 5/37 个采用任务发生回退。PT 将此类历史数据和原始测试范围一起展示，只接受有当前任务条件证明的配对增益参与同等覆盖时的排序。[配对复算](results/pt-2026-controls.json)

最多十个通过筛查的候选使 PT 能精确比较同预算集合。比较固定候选池中的能力并集大小，覆盖不增加时沿用原排名。5,400 个模拟候选池共 296 个补齐额外能力，零损失符合算法条件。三个返回预算下选择器的 P95 均低于 0.24 ms。模拟以已核实的离散能力为输入，端到端组合执行与依赖冲突另需试验。

## 复核入口

- [中文双栏论文](pt-framework-paper.pdf)及[源文件](pt-framework-paper.tex)
- [上线接口和推荐证据](pt-framework.md)
- [在线对照脚本](../scripts/evaluate_pt_iteration.py)、[论文 R3 权重复算脚本](../scripts/evaluate_pt_r3_release.py)、[词法重排](../scripts/evaluate_pt_r3_fusion.py)、[配对记录和选法复算脚本](../scripts/evaluate_pt_controls.py)

线上成绩与下载时点有关。脚本可先采集，再用同一缓存对 420f191 和当前版本执行 --frozen，并通过 --query-report 将旧查询交给新核查流程。实际候选与逐条标注放在结果文件。运行配对脚本时传入固定版 experiment_final_selected_manifest.json。所有本轮指标均使用本地程序或公开无付费端点。
