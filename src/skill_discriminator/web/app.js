const byId = id => document.getElementById(id);
const api = "/api/discriminator";
let busy = false;
let latestReport = null;

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}

function panel(title, description, extraClass = "") {
  const section = node("section", undefined, `card ${extraClass}`);
  section.append(node("h3", title));
  if (description) section.append(node("p", description));
  return section;
}

function table(headers, rows) {
  const frame = node("div", undefined, "table-wrap");
  const grid = node("table");
  const head = grid.createTHead().insertRow();
  for (const title of headers) {
    const cell = node("th", title);
    cell.scope = "col";
    head.append(cell);
  }
  const body = grid.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
    for (const field of row) tr.append(node("td", String(field)));
  }
  frame.append(grid);
  return frame;
}

async function request(route, payload) {
  const options = payload === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  };
  const response = await fetch(`${api}${route}`, options);
  const data = await response.json();
  if (!response.ok) {
    const error = Array.isArray(data.detail) ? data.detail.map(x => x.msg).join("，") : data.detail;
    throw new Error(error || `请求未完成（${response.status}）`);
  }
  return data;
}

function status(id, message, failed = false) {
  const text = byId(id);
  text.textContent = message;
  text.classList.toggle("error", failed);
}

function conditions() {
  const network = byId("network").value;
  return { task: byId("task").value, environment: { network_allowed: network ? network === "yes" : null } };
}

function invalidate() {
  latestReport = null;
  byId("export-report").disabled = true;
  byId("report-region").hidden = true;
  byId("candidates").replaceChildren();
}

function setBusy(value) {
  busy = value;
  byId("evaluate-link").disabled = value;
  byId("inspect-file").disabled = value;
  byId("skill-url").readOnly = value;
  byId("task").readOnly = value;
  byId("network").disabled = value;
  byId("skill-file").disabled = value;
}

function evidenceLine(parent, item) {
  if (!item) return;
  if (item.path) parent.append(node("p", `${item.path}${item.start_line ? `:${item.start_line}` : ""}`, "muted"));
  if (item.quote) parent.append(node("pre", item.quote));
  if (item.receipt_id) parent.append(node("p", `实测记录指纹 ${item.receipt_id}`, "muted break"));
}

function adoptionPanel(proof) {
  const result = proof.result;
  const holdout = proof.holdout;
  const section = panel("已登记版本的实测效果", proof.holdout_observation_zh, "measurement");
  section.id = "paired-evidence";
  section.append(table(["输入范围", "无 Skill", "读入 Skill", "改善 / 回退"],
    [...proof.strata, result, holdout].map(item => [item.title, `${item.without_passed}/${item.pairs}`,
      `${item.with_passed}/${item.pairs}`, `${item.beneficial} / ${item.harmful}`])));
  section.append(node("p", proof.scope, "muted"));
  const examples = node("details", undefined, "finding");
  examples.append(node("summary", "查看失败样本和原因"));
  for (const item of proof.boundary_examples) {
    for (const failure of item.with_failure) {
      const line = failure.event ? `事件 ${failure.event.id}，工位 ${failure.event.station}。候选 ${failure.eligible_codes.join("、") || "无"}。预期 ${failure.expected}，实际 ${String(failure.actual)}。` : failure.explanation;
      examples.append(node("p", `${item.id} · ${line}`));
    }
  }
  section.append(examples);
  return section;
}

function renderReport(report) {
  const root = byId("report");
  const warning = ["repair_required", "configuration_required", "task_contract_review"].includes(report.decision);
  root.replaceChildren(panel(report.decision_zh, report.reason, `verdict${warning ? " warning" : ""}`));
  root.append(panel(report.introduction.name, report.introduction.description));

  const proof = report.verified_adoption;
  if (proof?.state === "measured_on_registered_inputs") {
    root.append(adoptionPanel(proof));
  } else {
    root.append(panel("实际效果待验证", proof?.reason || "这份本地材料尚无可绑定的配对实测。"));
  }

  const origin = report.source_import;
  if (origin) {
    const source = panel("来源与读取范围");
    source.append(node("p", `${origin.repository} / ${origin.skill_path}`, "break"));
    source.append(node("p", origin.commit ? `固定提交 ${origin.commit}` : "本次版本未固定。", "break muted"));
    source.append(node("p", `已读取 ${origin.files_read} 个文件。${origin.inventory_complete ? "已取得包清单。" : "完整清单待核对。"}`));
    if (origin.omitted_count) source.append(node("p", `另有 ${origin.omitted_count} 项未经内容检查。`, "muted"));
    root.append(source);
  }

  const notes = panel("具体依据");
  for (const finding of [...report.package.findings, ...report.package.review_items, ...report.task_contract_review].slice(0, 16)) {
    const entry = node("div", undefined, "finding");
    entry.append(node("strong", finding.explanation));
    evidenceLine(entry, finding.evidence || finding);
    notes.append(entry);
  }
  for (const item of report.evidence_assessment.criteria) {
    const detail = node("details", undefined, "finding");
    detail.append(node("summary", `${item.title} · ${item.display}`), node("p", item.reason),
      node("p", item.formula, "muted"), node("p", item.scope, "muted"));
    for (const ref of item.evidence) evidenceLine(detail, ref);
    notes.append(detail);
  }
  root.append(notes);
  latestReport = report;
  byId("export-report").disabled = false;
  byId("report-region").hidden = false;
  byId("report-region").focus();
}

async function scanLink(skillPath, revision) {
  if (busy) return;
  const url = byId("skill-url").value.trim();
  if (!url) return;
  invalidate();
  setBusy(true);
  status("link-status", "正在读取和核对版本…");
  try {
    const data = await request("/import-url", { url, skill_path: skillPath, revision, ...conditions() });
    if (data.state === "selection_required") {
      status("link-status", `找到 ${data.candidate_count} 个 Skill，请选择要评估的文件。`);
      for (const item of data.candidates) {
        const button = node("button", item.path, "secondary");
        button.type = "button";
        button.onclick = () => scanLink(item.path, data.source.commit);
        byId("candidates").append(button);
      }
      return;
    }
    renderReport(data.report);
    status("link-status", "链接评估完成。");
  } catch (error) {
    status("link-status", error.message, true);
  } finally {
    setBusy(false);
  }
}

function example(url) {
  if (busy) return;
  byId("skill-url").value = url;
  scanLink();
}

async function loadStudies() {
  try {
    const data = await request("/adoption-studies");
    if (data.state !== "ready") throw new Error("未取得随包的实验记录。");
    const registered = byId("registered-example");
    registered.disabled = false;
    registered.onclick = () => example(data.example_url);
    const verify = panel("同一批真实产物的验收", "对照事先登记的任务答案。64 份模型产物来自一个公开 Skill 的 32 项配对输入。");
    const labels = { exact_constraints: "规则验收", standard: "Flash 直接评审", procedure: "Flash 逐步评审", deepseek_pro_retry: "Pro 补评后" };
    const grading = data.results.matched_public_artifacts;
    verify.append(table(["方法", "判对", "误放行", "缺失"], Object.entries(labels).map(([key, title]) => {
      const row = grading[key];
      return [title, `${row.correct}/${row.total}`, row.false_accept, row.missing];
    })));
    const signed = data.utility_study.signed;
    const comparison = panel("同预算的功能效果筛查", `${signed.rows} 个 Skill 与运行环境组合。每种方法使用两次试跑判断第三项任务。`);
    const scores = {release_prg: "SkillAudit 发布增益", skilltester_import: "SkillTester 公式移植", signed_mean: "逐项复算保留正负"};
    comparison.append(table(["方法", "前排改善", "复查找到回退", "平均误差"], Object.entries(scores).map(([key, label]) => {
      const row = signed.methods[key];
      return [label, `${row.review_positive}/${row.review_n}`, `${row.retest_negative}/${row.retest_n}`,
        row.mae === undefined ? "不适用" : `${(100 * row.mae).toFixed(2)} 个百分点`];
    })));
    byId("study-results").replaceChildren(verify, comparison);
  } catch (error) {
    byId("study-results").textContent = error.message;
  }
}

byId("link-form").onsubmit = event => { event.preventDefault(); scanLink(); };
for (const id of ["skill-url", "task", "network", "skill-file"]) byId(id).addEventListener("input", invalidate);
byId("public-example").onclick = () => example("https://github.com/anthropics/skills/tree/main/skills/pdf");

byId("inspect-file").onclick = async () => {
  if (busy) return;
  const file = byId("skill-file").files[0];
  if (!file) return status("file-status", "请选择 SKILL.md。", true);
  if (file.size > 600000) return status("file-status", "SKILL.md 需小于 600 KB。", true);
  invalidate();
  setBusy(true);
  try {
    const report = await request("/inspect", {files: {"SKILL.md": await file.text()}, ...conditions()});
    renderReport(report);
    status("file-status", "文档检查完成。");
  } catch (error) {
    status("file-status", error.message, true);
  } finally {
    setBusy(false);
  }
};

byId("recheck").onclick = async () => {
  const button = byId("recheck");
  button.disabled = true;
  status("recheck-status", "正在逐条复核归档产物…");
  try {
    const result = await request("/evidence/recheck");
    status("recheck-status", `${result.correct}/${result.artifacts} 项判定与登记答案一致。无 Skill 通过 ${result.passed.no_skill}/${result.pairs}，有 Skill 通过 ${result.passed.skill}/${result.pairs}。`);
  } catch (error) {
    status("recheck-status", error.message, true);
  } finally {
    button.disabled = false;
  }
};

byId("export-report").onclick = () => {
  if (!latestReport) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(latestReport, null, 2)], {type: "application/json"}));
  const link = node("a");
  link.href = url;
  link.download = "skill-assessment.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

loadStudies();
