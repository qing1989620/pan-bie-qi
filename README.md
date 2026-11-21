# 开源 Skill 判别器

粘贴公开 Skill 链接，查看功能声明、运行条件、源码问题，以及该版本已有的实际效果。报告中的结论可追溯到文件位置、原始指纹和已登记的产物验收。没有配对实测的版本显示「实际效果待验证」。

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

1. 点击「工位标签 · 已有实测」。随包提供已核对的公开版本快照，离线也能看到配对结果。32 个输入中，无 Skill 通过 15 个，读入说明后通过 16 个。留出输入两组均为 8/16。
2. 展开「查看失败样本和原因」。报告会列出同工位多个编码、没有合法编码等失败条件，以及该事件的期望与实际输出。
3. 点击「复核 64 份产物」。程序重新读取固定输入和产物，执行独立的规则验收，校验与登记正确答案的符合数。
4. 粘贴其他 GitHub 仓库、技能目录、`SKILL.md` 或 `skills.sh` 链接。多个候选时选择一项。公开链接通过 HTTPS 读取，GitHub 限流时可能仅取得单份说明或报告读取失败。也可以在页面检查本地 `SKILL.md`。
5. 下载 JSON 报告以保留判定依据与版本指纹。有效的对比限定于报告指明的模型、输入和任务规则。

## 文档与复核

- [使用与 API](docs/usage.md)
- [判定方法、数据与实测比较](docs/evaluation.md)
- [中文双栏论文](docs/skill-discriminator-paper.pdf)
- [第三方材料说明](docs/third-party.md)

复核随包产物可直接运行 `python -m skill_discriminator.verify`。它打印固定档案的通过数、留出结果和数据指纹。开发测试安装 `python -m pip install ".[dev]"` 后运行 `python -m pytest -q`。
