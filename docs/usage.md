# 使用与 API

## 看懂报告

页面显示 Skill 声明的功能、已取得的 GitHub 提交、可读取的文件与清单覆盖。报告中的源码片段是待分析材料。公开链接的读取范围包括 GitHub 仓库、`tree` 目录、`blob` 或 `raw` 的 `SKILL.md`，以及 `skills.sh` 的公开目录条目。仓库含多个 Skill 时，选择实际要评估的路径。

填写任务描述可以补充用户的输入、输出或运行条件。源文件和声明能解释已确认的问题或待复核线索。软件只把与仓库、提交、路径和文档 SHA-256 均一致的配对实测附到对应报告。若仅从原始文档端点取得说明，页面会显示读取范围，保留版本和完整清单待核验。

「历史配对实测」同时给出无 Skill 和有 Skill 的通过数，单列改善、回退、留出成绩和失败样本。当前随包登记一个真实开源 Skill 版本。填写新任务后，报告首先显示该任务的验证状态。归档仍可展开，范围限定于登记输入。链接检查和归档复核都通过本地服务进行。

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

返回 `selection_required` 时，在下一次请求同时传回原始 `url`、候选的 `path` 作为 `skill_path`，以及响应 `source.commit` 作为 `revision`。这样再次获取的材料仍对应原提交。链接评估成功时报告在响应的 `report` 中。界面导出相同的报告 JSON。

`inspect` 接收 `files` 字典，键为包内相对路径，值为文本，至少提供 `SKILL.md`。可选 `inventory` 和 `inventory_complete` 用于说明清单。两个检查接口都接受 `task` 与 `environment`。`environment.available_keys` 只接受变量名称到布尔值的映射，例如 `{"LITERATURE_API_KEY": false}`。`environment.network_allowed` 记录目标环境的联网状态。

`task/pdf-table` 接收 `pdf_base64`、从 1 开始的 `page` 以及可选的 `reference_csv`、`headers` 和 `expected_rows`。成功提取时，`state` 为 `extracted`，`verification.state` 则给出参考核对或待核对状态。没有可读文字层时返回 `needs_ocr`，多表歧义或找不到表格时也会给出单独的原因。接口处理内存中的本地输入，结果中的 `file_sha256` 可用于识别已试跑的文件。

默认启动绑定 `127.0.0.1`。如需给只读检查接口加令牌校验，可以在启动进程的环境中设置 `PAN_BIE_QI_TOKEN`，API 客户端调用时在请求头提供 `X-Discriminator-Token`。浏览器页面的默认试用使用未配置令牌的本地实例。
