"""A transparent evidence ledger; unmeasured quality never receives points."""
from __future__ import annotations

from .inspection import citation

METHOD = "evidence-ledger-v1"


def model_validation():
    return {"state": "not_enabled", "score_enabled": False,
            "reason": "独立版根据已取得的材料和已登记的实测给出判断。"}


def build(report, files):
    package = report["package"]
    criteria = []

    def criterion(identity, title, state, value, why, formula, evidence, scope, action, **extra):
        criteria.append({"id": identity, "title": title, "state": state, "display": value,
                         "reason": why, "formula": formula, "evidence": evidence,
                         "scope": scope, "next_action": action, **extra})

    py = sorted(p for p in files if p.endswith(".py"))
    syntax_bad = [f for f in package["findings"] if f["kind"] in {"python_syntax_error", "entrypoint_syntax_error"}]
    bad_paths = {f["evidence"]["path"] for f in syntax_bad}
    unknown_paths = {r.get("evidence", {}).get("path") for r in package["review_items"] if r["kind"] == "python_parse_out_of_scope"}
    syntax_pass = len(set(py) - bad_paths - unknown_paths)
    criterion("python_syntax", "Python源码语法", "failed" if bad_paths else "unknown" if unknown_paths else "checked" if py else "not_applicable",
              f"{syntax_pass}/{len(py)} 个文件可解析" if py else "本包没有已读取的Python文件",
              "用宿主Python解析当前文本；通过只说明语法可解析，不能证明任务实现正确",
              "可解析文件数 / 已读取Python文件数；未读文件不参与分母",
              [f["evidence"] for f in syntax_bad] or [citation(files, p, 1) for p in py[:3]],
              "当前版本、当前Python语法；不检查JavaScript、Shell或外部依赖", "修复语法错误后，另用独立产物判据运行任务", numerator=syntax_pass, denominator=len(py))

    observations = package["command_observations"]
    accepted = [r for r in observations if r["result"]["status"] == "consistent_static"]
    rejected = [r for r in observations if r["result"]["status"] == "inconsistent_static"]
    unknown = len(observations) - len(accepted) - len(rejected)
    checked = len(accepted) + len(rejected)
    criterion("documented_cli", "文档命令与接口是否相容", "failed" if rejected else "partially_checked" if checked and unknown else "checked" if checked else "unknown",
              f"{len(accepted)}/{checked} 条已核验命令语法相容，{unknown} 条待验证" if observations else "没有可核验的字面Python命令",
              "从源码字面声明重建参数语法，再让可信argparse判定文档命令；拒绝原因直接来自解析器。动态声明与自定义转换器保留未知",
              "语法相容率 = 相容命令 / (相容 + 冲突)；覆盖率 = (相容 + 冲突) / 文档识别命令；未知不算通过",
              [citation(files, "SKILL.md", r.get("start_line", 1)) for r in observations[:8]],
              "仅SKILL.md中识别到的直接Python命令；目标应使用同版本标准库argparse；不代表端到端运行成功",
              "按下方解析器原因修正文档参数，随后验证真实输出", numerator=len(accepted), denominator=checked,
              unknown=unknown, total=len(observations), observed_rate=len(accepted)/checked if checked else None,
              coverage=checked/len(observations) if observations else None)

    history = report.get("tested_evidence")
    criterion("functional_correctness", "实际功能是否正确", "observed_failure" if history and history["passed"] < history["n"] else "tested_within_scope" if history else "unknown",
              f"{history['passed']}/{history['n']} 个已记录输入通过" if history else "待验证：没有匹配版本的可信执行记录",
              "仅采纳服务登记、版本匹配的实际产物验收记录；用户文档中的自报通过无证明权限",
              "观测通过率 = 独立检查通过输入数 / 该套件实际输入数；固定测试集不提供总体成功概率置信区间",
              [{k: history.get(k) for k in ["source", "receipt_id", "receipt_sha256", "task_family"]}] if history else [],
              history["scope"] if history else "输入、模型、宿主和真实运行都未证实，不能从文档外推功能优劣",
              "使用针对声明能力的任务、属性和边界输入；以独立检查器验收产物", numerator=history["passed"] if history else None,
              denominator=history["n"] if history else None, observed_rate=history["passed"]/history["n"] if history else None,
              confidence_interval=None, generalization_probability=None)

    runtime = report.get("runtime_policy") or {}
    leads = report.get("capability_analysis", {}).get("leads", [])
    criterion("runtime_safety", "运行时行为与安全", "veto" if runtime.get("hard_block") else "not_certified",
              "已观测到违反明确声明的调用" if runtime.get("hard_block") else f"{len(leads)} 条静态能力线索；尚无通用安全认证",
              "实际宿主观测到的声明违约具有否决权限；源码里出现写文件或联网调用只算待核查线索，未触发不能视为违规",
              "不混入功能得分；调用被拒绝与副作用成功分别记录",
              [r["evidence"] for r in leads[:5] if r.get("evidence")],
              "运行证据仅限登记输入和WASI可观测调用；外部API、其他宿主及未执行分支仍未知",
              "在目标宿主记录轨迹、文件与网络行为，再做声明与实际行为对照", safety_score=None)

    conditions = report["conditions"]
    unmet = [r for r in conditions if r["state"] == "unmet"]
    met = sum(r["state"] == "met" for r in conditions)
    criterion("environment", "运行要求是否具备", "unmet" if unmet else "user_reported" if conditions else "unknown",
              f"用户已说明具备{met}/{len(conditions)}项要求" if conditions else "未提取到受支持格式的明确要求",
              "逐项对照作者声明与用户提供的配置状态；未连接主机认证这些配置。没有声明不代表没有依赖",
              "逐项显示具备 / 不具备 / 未说明，不折算质量分", [r["evidence"] for r in conditions],
              "元数据requires字段及用户输入；不是环境探测结果", "核对依赖版本、网络和环境变量，再在目标环境运行")

    criterion("marginal_utility", "相对不使用Skill是否更好", "unknown", "未测量：没有同条件配对实验",
              "文档相似、语法通过和脚本输出正确，都不能单独证明给大模型带来收益",
              "收益Δ = 同一模型、宿主、任务与预算下，有Skill成功率 − 无Skill成功率；同时报告配对不确定性、token和时间",
              [], "当前链接评估未运行大模型，不能填充收益、成本或推荐优胜分", "冻结任务与独立验收器，随机化运行顺序，比较有/无Skill并保留回退任务",
              utility_gain=None, token_delta=None, latency_delta=None)

    bounds = report["introduction"]["declared_exclusions"]
    criterion("boundary", "能力边界是否经过验证", "author_claim_only" if bounds else "unknown",
              "已提取作者声明；执行边界仍待验证" if bounds else "未找到明确边界声明，不能外推为无限能力",
              "边界由输入契约、任务、宿主、依赖、版本与已测样本共同限定；文档声明本身不证明真实边界",
              "声明范围与执行证据分别保留，不给声明数量加质量分", bounds,
              "已测试通过只能覆盖登记输入；反例能否决对应声明，不能否决所有用途", "补充失败模式、超出范围输入及未知项")

    trace = []
    for finding in package["findings"]:
        trace.append({"rule": finding["kind"], "effect": "requires_repair_in_stated_scope", "reason": finding["explanation"], "evidence": finding.get("evidence")})
    if runtime.get("hard_block"):
        trace.insert(0, {"rule": "observed_policy_violation", "effect": "veto", "reason": "匹配版本的宿主轨迹记录了与明确禁止声明冲突的调用"})
    if not trace:
        trace.append({"rule": "insufficient_end_to_end_evidence", "effect": "no_global_acceptance", "reason": "没有已确认的问题不等于质量优秀；未测功能、收益与安全仍保持待验证"})
    return {"method": METHOD, "overall_score": None,
            "overall_score_reason": "不同证据的测量对象不同，尚无经独立验证的合成权重；不把未知项变成分数，也不把语法通过率当整体质量",
            "criteria": criteria, "decision_trace": trace, "model_validation": model_validation(),
            "confidence_policy": "可信度来自证据类型、版本与可复核范围；不给主观置信百分比",
            "research_status": "限定规则已做工程差分验证；尚未完成自然社区Skill整体质量的独立盲测，未证明超过现有论文"}
