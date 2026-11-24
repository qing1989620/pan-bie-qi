# 开源 Skill 判别器

粘贴一个公开 Skill 链接，查看说明有多少字符、适合做什么、实测做到了哪一步、在哪些输入上失效，以及和同类公开 Skill 的差别。报告给出限定于证据范围的质量结论，每项依据可查到说明原文、版本指纹或产物验收结果。

## 启动网页

需要 Python 3.11 或更新版本。

```powershell
git clone https://github.com/qing1989620/pan-bie-qi.git
cd pan-bie-qi
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m skill_discriminator
```

macOS 或 Linux 下，克隆并进入仓库后运行以下命令。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m skill_discriminator
```

打开 [本地判别器](http://127.0.0.1:8853/)。端口被占用时，启动命令可附加 `--port 8860`。程序和网页均由此包提供。默认无需模型账号或 API 密钥。

## 试用

1. 点击「工位标签 · 已有实测」。说明可离线读取，常规匹配 8/8，同工位多码与无合法匹配各 0/8，留出输入有无 Skill 均为 8/16。可展开查看期望和实际结果。
2. 点击「PDF Skill · 流程实测」。对照三个公开版本。常规文字提取的局部复跑为 3/4，两个 Skill 的默认网格提表均为 2/6。页面显示失败的输入类型及原文出处。
3. 点击「XLSX Skill · 同类对比」。查看工作簿、公式、CSV 和图表的说明及其依据。这个版本目前展示说明对比，任务效果待验证。
4. 粘贴其他 GitHub 仓库、技能目录、`SKILL.md` 或 `skills.sh` 链接。多个候选时选择一项。GitHub 限流时可能仅取得单份说明。可以检查本地 `SKILL.md`，也可以在报告里加入另一个公开链接作对照。
5. 下载 JSON 保留能力条目、证据、固定版本及对照结果。点击「复核 64 份产物」可以再次验收归档的配对结果。
6. 点击「cuDF Skill · 公开实测」核查外部配对结果。包内资源检查在取得清单后判断说明链接是否指向实际资源。

## PDF 表格试跑

在页面展开「单独试跑 PDF 表格任务」，上传 PDF 并指定表格所在页。用参考 CSV 可以核对每一格，直接从网页下载提取结果。无文字层的页面会提示需要 OCR。

可以用下方两个文件复测。上传论文，页码填 **4**，再上传参考 CSV，预期核对 **4 行、16 个数据格**。

- [输入论文 PDF](docs/skill-discriminator-paper.pdf)
- [表 6 参考 CSV](docs/examples/paper-table-6.csv)

这项试跑验证当前文件上的本地提取结果。Skill 的配对实测仍显示在「历史配对实测」。

## 文档与复核

- [使用与 API](docs/usage.md)
- [判定方法、数据与实测比较](docs/evaluation.md)
- [单个 Skill 的能力报告、判定规则与同类对照](docs/skill-profile-evaluation.md)
- [未见 Skill 的留出实验与强基线对照](docs/unseen-skill-evaluation.md)
- [真实任务的改前改后验收](docs/field-evaluation.md)
- [中文双栏论文](docs/skill-discriminator-paper.pdf)
- [第三方材料说明](docs/third-party.md)

复核随包产物可直接运行 `python -m skill_discriminator.verify`。它打印固定档案的通过数、留出结果和数据指纹。开发测试安装 `python -m pip install ".[dev]"` 后运行 `python -m pytest -q`。
