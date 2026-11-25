# 使用与 API

## 看懂报告

访问 `/user` 使用简洁版。输入公开链接可看说明长度、适合的用途、使用前应处理的问题和六项评分。「查看评分依据」列出命中和缺项，带来源行号。「查看详细报告」可打开同一次评估的能力画像、实测范围、失败案例和同类比较。仓库根链接只有一个非第三方 Skill 时，网页自动评估它。多个独立 Skill 时，用下拉框选定一项。

页面首先显示单个 Skill 的说明长度、功能质量结论和分项能力。已测能力列出通过数和失败的输入。说明覆盖可逐条展开看依据。公开链接支持 GitHub 仓库、`tree` 目录、`blob` 或 `raw` 的 `SKILL.md`，以及 `skills.sh` 的公开条目。仓库含多个 Skill 时，选择要评估的路径。

填写任务描述可以补充用户的输入、输出或运行条件。源文件和声明能解释已确认的问题或待复核线索。软件只把与仓库、提交、路径和文档 SHA-256 均一致的配对实测附到对应报告。若仅从原始文档端点取得说明，页面会显示读取范围，保留版本和完整清单待核验。

「历史配对实测」同时给出无 Skill 和有 Skill 的通过数，单列改善、回退、留出成绩和失败样本。填写新任务后，可独立查看该任务的验证状态。同类公开 Skill 卡片显示同规则检查结果、固定说明和共有的局部实测，也可以手动填入另一个公开链接。[能力报告方法与实测范围](skill-profile-evaluation.md)

## PDF 表格试跑

输入 PDF Skill 的链接，并说明要做的是表格提取或扫描件识别。相关证据来自已读取的说明文件，报告会显示工具路径、源文件行号和依赖线索。点击「用自己的 PDF 试跑」可对具体文件进行验证。

「PDF 表格试跑」接收 4 MB 以内的单个 PDF 和目标页码。优先尝试按网格提取。无竖线的表可以提供参考 CSV，或同时填写用 `|` 分隔的列名和预期行数。CSV 参考使用 UTF-8，第一列的行标签应非空、唯一。报告提供提取方法、表格预览、行列数、CSV 下载，以及与参考不一致的样本。扫描图像页提示需要 OCR 和相应语言包。

使用[论文 PDF](skill-discriminator-paper.pdf)第 4 页及[参考 CSV](examples/paper-table-6.csv)即可测试。输出是本地解析器对这份 PDF 的结果。对于不同布局或缺少合适参考的文件，报告会保留待核对的结论。

## 本地接口

启动说明见 [README](../README.md)。接口定义可在 `http://127.0.0.1:8853/docs` 查看。

| 方法与路径 | 输出 |
| --- | --- |
| `GET /api/discriminator/health` | 服务版本与就绪状态 |
| `POST /api/discriminator/import-url` | 读取后的报告，或同一仓库下的候选列表 |
| `POST /api/discriminator/inspect` | 对用户提供的文本快照进行只读检查 |
| `GET /api/discriminator/evidence/recheck` | 重新验收固定案例下的 64 份归档产物 |
| `GET /api/discriminator/adoption-studies` | 页面使用的实测与公开基线对照汇总 |
| `POST /api/discriminator/task/pdf-table` | 提取指定页的表格，按可选参考验收并返回 CSV |

在 PowerShell 中可用以下命令调用链接接口。

```powershell
$body = @{ url = 'https://github.com/anthropics/skills/tree/main/skills/pdf' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8853/api/discriminator/import-url' -ContentType 'application/json' -Body $body
```

返回 `selection_required` 时，在下一次请求同时传回原始 `url`、候选的 `path` 作为 `skill_path`，以及响应 `source.commit` 作为 `revision`。这样再次获取的材料仍对应原提交。链接评估成功时，在 `report.skill_profile` 获取 `size`、`capabilities`、`functional_quality`、`documentation`、`measurements` 及 `comparison`。界面导出的 JSON 还包含手动添加的对照链接及结果。

可选的 `prefer_main: true` 启用唯一主 Skill 规则。根目录有多个候选且只有一个位于常见第三方目录之外时，接口自动评估该项，在 `source.selection` 保留候选。简洁版用 `report.quick_summary` 读取用途、结论和理由。

遇到 GitHub 匿名读取限流时，部署者可在服务进程设置 `PAN_BIE_QI_GITHUB_TOKEN`。凭据只用于固定 GitHub API 主机的仓库读取请求，页面仍只接受公开 Skill。

`report.package.resource_links` 列出受支持的包内引用、来源行号和 `present`、`missing` 或 `unknown` 状态。完整 Git 清单支持确认缺失。匹配 NVIDIA 发布快照时，`report.skill_profile.public_benchmark` 提供作者配对得分、任务数、任务集指纹和原报告链接。留出预测研究见[未见 Skill 实验](unseen-skill-evaluation.md)。

### 分项评分与 Agent 接入

`report.assessment.dimensions` 包含每项的分数、依据和范围。`report.assessment.rubric_version` 标识量表版本。在用途边界、流程产出、运行准备、验收异常四项中，命中一条规则得 25 分。包内一致性显示可静态判定的检查通过比例，未核实项另计。任务实测显示固定版本的登记留出通过率或外部发布效果分。`score: null` 表示该维度暂无得分。

Agent 可先调用 `/import-url`，再读取 `assessment.agent`。使用提交、路径和包指纹关联版本，读取 `task_scope` 核对任务范围。以下状态可用于目录预检、提示缺失配置和安排人工复核。

| `status` | 含义 |
| --- | --- |
| `repair_required` | 静态核查已确认缺陷 |
| `environment_mismatch` | 当前环境缺少声明的条件 |
| `sensitive_material_review` | 包内配置材料需复核 |
| `configuration_unknown` | 声明的环境条件尚未核实 |
| `task_review_required` | 当前任务与声明存在待核对项 |
| `measured_scope_available` | 存在可引用的实测范围 |
| `runtime_review_required` | 运行前需要核对任务范围与环境 |

`automatic_execution_authorized` 恒为 `false`。Agent 宿主负责自己的授权和运行隔离。

`inspect` 接收 `files` 字典，键为包内相对路径，值为文本，至少提供 `SKILL.md`。可选 `inventory` 和 `inventory_complete` 用于说明清单。两个检查接口都接受 `task` 与 `environment`。`environment.available_keys` 只接受变量名称到布尔值的映射，例如 `{"LITERATURE_API_KEY": false}`。`environment.network_allowed` 记录目标环境的联网状态。

`task/pdf-table` 接收 `pdf_base64`、从 1 开始的 `page` 以及可选的 `reference_csv`、`headers` 和 `expected_rows`。成功提取时，`state` 为 `extracted`，`verification.state` 则给出参考核对或待核对状态。没有可读文字层时返回 `needs_ocr`，多表歧义或找不到表格时也会给出单独的原因。接口处理内存中的本地输入，结果中的 `file_sha256` 可用于识别已试跑的文件。

默认启动绑定 `127.0.0.1`。如需给只读检查接口加令牌校验，可以在启动进程的环境中设置 `PAN_BIE_QI_TOKEN`，API 客户端调用时在请求头提供 `X-Discriminator-Token`。浏览器页面的默认试用使用未配置令牌的本地实例。
