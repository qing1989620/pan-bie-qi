# PT 框架

启动服务后进入找 Skill 页面，输入目标与限制。例如输入「用 Matplotlib 生成散点图并导出 SVG」。推荐卡展示匹配能力、判别器的结论及采用条件，展开后可查看六维评分和依据。「查看完整评估」打开同一固定版本的判别详情。[找 Skill](http://127.0.0.1:8853/recommend)

## 任务到推荐

[论文与 Xinxin 接入案例](pt-framework-paper.pdf) · [可编辑框架图原件](figures/pt-framework-v1.7.0.pptx) · [框架图矢量 PDF](figures/pt-framework-v1.7.0.pdf)

| 模块 | 输入 | 返回 |
| --- | --- | --- |
| 搜索 `pt_discovery.py` | 从任务识别的格式和操作，经中英文规则得到的短查询 | skills.sh、参考目录及可选 DeepSeek 联网工具的候选与查询状态 |
| 判别 `pt.py` 和 `service.py` | 固定提交的说明、许可、清单与可读取的包内文本 | 证据行、硬性不匹配、六项判别结果及可绑定的历史实测 |
| 推荐 `pt.py` | 有依据的候选及尚未覆盖的能力 | 优先补齐任务能力的几项选择、适用条件、替代项与缺口 |

搜索侧使用最多四组初始查询词，最多核查十个公开候选。不同能力的候选交替进入核查列表。候选先通过说明、许可和任务匹配初筛，再读取固定提交的包内文本，调用单链接评估也使用的 `service.assess_imported`。判别器可以发现文档命令与脚本参数声明不一致等问题，并给出筛除依据。含 `[--flag]` 等可选参数记号的命令会标为未核实。读取超时、材料不全或版本不一致均有独立状态。覆盖是对说明证据的判断，历史实测绑定发布版本和测试范围。

明确指定工具、操作和交付格式时，判别核查这些要求对应的来源行。例如，将 Mermaid 作为输入的转换器无法同时满足生成 Mermaid 的条件。推荐在同一数量预算下比较候选集合的能力并集。覆盖不增加时保留原排名。历史配对结果注明任务和运行环境，参与同等覆盖时的排序需要当前任务匹配证据。实际串联效果仍需使用当前输入验证。

## Agent 接口

调用 `POST /api/discriminator/recommend`。默认返回最多三项。

```json
{
  "task": "用 Matplotlib 生成散点图并导出 SVG",
  "environment": {"network_allowed": false},
  "limit": 3,
  "search_online": true,
  "use_model": false
}
```

`network_allowed` 表示目标 Agent 的运行环境。`search_online: false` 关闭公开目录搜索，仍会尝试从 GitHub 核查参考链接。使用内置参考候选的请求也保留来源与读取状态。

- `requirements` 说明需要哪些能力、结果里哪些有匹配依据。
- `recommendations[].matches` 提供匹配说明及固定提交中的行链接。如果主要操作和产出依据位于不同行，`output_evidence` 提供第二处链接。`conditions_to_check` 给出采用前的条件。
- `recommendations[].assessment` 提供同一个判别器的结论、六维评分、评分理由和文件读取范围。`scope: package_text_partial` 表示包内材料有未检查项。
- `recommendations[].assessment.task_evidence` 保留测试范围、得分和出处，`applies_to_current_task` 表明实测是否覆盖当前任务条件。
- `pipeline` 提供搜索、筛除及来源未核全的记录。已确认的包内问题提供固定提交的证据行。`no_supported_candidates` 是正常返回。

可以把这个 HTTP 接口注册为模型宿主的只读工具。宿主用 `source.commit`、`source.skill_path` 和 `document_sha256` 保存版本，用 `requirements` 选择或补充能力。之后的安装、配置和运行由宿主管理。部署时可用已有的 `PAN_BIE_QI_TOKEN` 保护接口。

## 模型接入

Xinxin 在用户点击查找后调用 Python 接口 `pt.recommend`，仅在宿主配置官方 DeepSeek 服务时，从后端注入 `deepseek_api_key`。PT 最多发起一次 `deepseek-flash` 请求，从实际完成的 `deepseek_web_search` 工具结果读取 GitHub 链接。公开目录与参考候选可独立提供结果。Xinxin 最多展示三项建议，选用后按固定版本作为本轮引用。[宿主集成实测与说明](https://github.com/qing1989620/xinxin-local/blob/5c870edfb649409f876b04ce364c8f8ab1f17009/docs/pt-chat-recommendations.md)

默认使用本地任务规则、公开目录和可复核的匹配证据。要使用 DeepSeek 补充关键词，在本地服务进程设置 `PAN_BIE_QI_DEEPSEEK_API_KEY`，然后在网页或请求中选择启用。适配器调用 `deepseek-flash`，单个请求最多尝试一次，最多生成 140 token。模型接收任务描述并返回词表。认证、余额、超时和输出错误会回退到本地检索。[DeepSeek API](https://api-docs.deepseek.com/api/create-chat-completion/)

本地多语言 E5 为实验选项。安装 `python -m pip install ".[semantic]"` 后，将固定导出的 `tokenizer.json` 和 `onnx/model_quantized.onnx` 放在本机目录中，模型文件使用 `model_quantized.onnx` 文件名。环境变量 `PAN_BIE_QI_E5_DIR` 指向该目录。程序在加载时核对 SHA-256。对照结果支持默认沿用简洁文本检索和证据门控。[固定 ONNX 导出](https://huggingface.co/Xenova/multilingual-e5-small/tree/761b726dd34fb83930e26aab4e9ac3899aa1fa78)

## 复核

在仓库根目录运行。可先省略 `--encoder` 复跑词法方法。两个本地模型实验均使用公开冻结数据。

```powershell
python scripts/evaluate_pt_retrieval.py --output tmp/pt-r3-retrieval.json
python scripts/evaluate_pt_retrieval.py --encoder tmp/pt-benchmark/e5 --output tmp/pt-r3-retrieval-e5.json
python scripts/train_pt_ranker.py --output tmp/pt-r3-group-ranker.json
python scripts/evaluate_pt_live.py --base http://127.0.0.1:8853 --output tmp/pt-live-integration.json
```

分组训练另外使用 `numpy` 和 `scikit-learn`，版本记录在结果。目录内容随发布变更，在线实验保存每个提供者的结果与固定提交。

- [公开 R3 检索实验](results/pt-r3-retrieval.json)
- [按 Skill 分组留出的轻量训练](results/pt-r3-group-ranker.json)
- [1.6.0 在线任务记录](results/pt-live-integration.json)
- [1.7.0 方法复现、对照和失败案例](pt-2026-iteration.md)
- [Xinxin Agent 1.8.3 点云案例与证据链接](results/pt-xinxin-live-2026-11-28.json)
- [论文及设计依据](pt-framework-paper.pdf)
