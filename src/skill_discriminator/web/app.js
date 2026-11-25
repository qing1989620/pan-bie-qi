const byId = id => document.getElementById(id);
const api = "/api/discriminator";
const activeReport = () => byId(document.documentElement.dataset.view === "user" ? "quick-result" : "report-region");
let busy = false;
let latestReport = null;

function syncView() {
  const view = location.pathname === "/user" ? "user" : "detail";
  document.documentElement.dataset.view = view;
  for (const link of document.querySelectorAll("nav [data-view-link]")) {
    if (link.dataset.viewLink === view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  if (latestReport) activeReport().focus();
}

document.addEventListener("click", event => {
  const link = event.target.closest?.("a[data-view-link]");
  if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  if (location.pathname !== link.pathname) history.pushState(null, "", link.pathname);
  syncView();
});
window.addEventListener("popstate", syncView);
syncView();

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
    for (const field of row) {
      const cell = node("td");
      cell.append(field instanceof Node ? field : String(field));
      tr.append(cell);
    }
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
  byId("quick-result").hidden = true;
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

function sourceLink(title, url) {
  const link = node("a", title);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function scoreCards(assessment) {
  const section = node("section", undefined, "quick-answer");
  section.append(node("h3", "分项评分"));
  const grid = node("div", undefined, "score-grid");
  for (const axis of assessment.dimensions) {
    const cell = node("article", undefined, "score-card");
    const head = node("header", undefined, "score-head");
    const numeric = typeof axis.score === "number";
    const value = node("span", numeric ? String(axis.score) : "—", "score-value");
    if (numeric) value.append(node("small", " / 100"));
    else value.setAttribute("aria-label", "暂无得分");
    head.append(node("h4", axis.label), value);
    cell.append(head);
    if (numeric) {
      const bar = node("div", undefined, "score-bar");
      bar.setAttribute("aria-hidden", "true");
      const fill = node("span");
      fill.style.width = axis.score + "%";
      bar.append(fill);
      cell.append(bar);
    }
    cell.append(node("p", axis.basis, "score-basis"), node("p", axis.reason, "score-reason"));
    if (axis.checks.length || axis.source_url || axis.scope) {
      const detail = node("details", undefined, "score-detail");
      detail.append(node("summary", "查看评分依据"));
      if (axis.scope) detail.append(node("p", axis.scope));
      const ordered = [...axis.checks].sort((left, right) => (
        Number(right.state === "missing") - Number(left.state === "missing")
      ));
      if (ordered.length) {
        const list = node("ul");
        for (const item of ordered) {
          const credit = item.max_points ? item.points + "/" + item.max_points + " 分"
            : ({present: "通过", missing: "发现问题", unknown: "未核实"})[item.state];
          const row = node("li", item.label + " · " + credit);
          evidenceLine(row, item.evidence);
          list.append(row);
        }
        detail.append(list);
      }
      if (axis.source_url) detail.append(sourceLink("原始评测报告", axis.source_url));
      cell.append(detail);
    }
    grid.append(cell);
  }
  section.append(grid);
  return section;
}

function profileOverview(profile, origin) {
  const hero = panel(profile.name, undefined, "profile-hero");
  hero.id = "skill-overview";
  hero.prepend(node("p", profile.category, "category"));
  const {size, documentation, functional_quality: quality} = profile;
  const number = value => value.toLocaleString("zh-CN");
  const metrics = node("dl", undefined, "profile-metrics");
  for (const [label, value, description] of [
    ["说明长度", `${number(size.document_characters)} 字符`, `正文 ${number(size.body_characters)} 字符 · ${number(size.document_lines)} 行`],
    ["文本材料", `${number(size.read_text_characters ?? size.document_characters)} 字符`,
      `已读 ${size.files_read} 个文件 · ${size.inventory_complete ? `包清单共 ${size.inventory_files} 项` : "包清单待核对"}`],
    ["说明覆盖", `${documentation.found}/${documentation.total}`, documentation.label],
  ]) {
    const metric = node("div");
    metric.append(node("dt", label), node("dd", value), node("p", description));
    metrics.append(metric);
  }
  const verdict = node("div", undefined, `quality-callout quality-${quality.state}`);
  verdict.append(node("h4", quality.title), node("p", quality.reason));
  if (quality.scope) verdict.append(node("p", quality.scope, "muted"));
  hero.append(metrics, verdict, node("p", profile.description, "author-description"));
  if (origin?.canonical_url) hero.append(sourceLink("查看原始说明", origin.canonical_url));
  return hero;
}

function publishedEvidence(proof) {
  const box = panel("公开配对评测", proof.scope, "measurement");
  box.id = "published-benchmark";
  const percent = value => `${(100 * value).toFixed(1)}%`;
  box.append(table(["检查维度", "无 Skill", "有 Skill", "净增益"], proof.measures.map(row => [
    row.label, percent(row.without_skill), percent(row.with_skill),
    `${row.lift > 0 ? "+" : ""}${(100 * row.lift).toFixed(1)} 个百分点`,
  ])));
  box.append(sourceLink("查看原始评测报告", proof.url));
  return box;
}

function resourceEvaluation(links, origin) {
  const resolved = links.filter(row => row.state === "present").length;
  const box = panel("包内资源检查", `定位到 ${resolved}/${links.length} 个说明链接指向的资源。`);
  box.id = "resource-evaluation";
  const status = {present: "已找到", missing: "完整清单中缺失", unknown: "清单不完整，待核对"};
  const detail = node("details", undefined, "capability-detail");
  detail.append(node("summary", "查看资源与说明位置"));
  detail.append(table(["说明引用", "检查结果", "依据"], links.map(row => {
    const position = `SKILL.md 第 ${row.evidence.start_line} 行`;
    return [row.resource, status[row.state], origin?.canonical_url ?
      sourceLink(position, `${origin.canonical_url}#L${row.evidence.start_line}`) : position];
  })));
  box.append(detail);
  return box;
}

function observationText(result) {
  return `${result.passed}/${result.total} 项通过`;
}

function workflowQuality(result) {
  if (result.passed === result.total) return "通过全部复跑输入";
  if (result.passed / result.total < .5) return "该流程表现欠佳";
  return "该流程有条件可用";
}

function capacityPanel(profile, origin) {
  const section = panel("能力与实测边界");
  section.id = "skill-capabilities";
  const list = node("div", undefined, "capability-list");
  for (const capability of profile.capabilities) {
    const item = node("article", undefined, "capability-item");
    const {observation, workflow_observation: workflow, evidence} = capability;
    const measured = observation || workflow;
    const state = measured ? observationText(measured) : capability.state === "declared" ? "说明有记录 · 效果待验证" : "说明章节 · 效果待验证";
    const style = measured ? (measured.passed === measured.total ? "success" : measured.passed === 0 ? "failure" : "partial") : "pending";
    const header = node("div", undefined, "capability-heading");
    header.append(node("h4", capability.title), node("span", state, `capability-state ${style}`));
    item.append(header);
    if (observation) item.append(node("p", `${observation.title} · 无 Skill ${observation.without}/${observation.total}`));
    if (workflow) {
      const failing = workflow.cases.filter(row => !row.passed).map(row => row.label);
      item.append(node("p", workflowQuality(workflow)));
      if (failing.length) item.append(node("p", `未通过：${failing.join("、")}。`));
      const details = node("details", undefined, "capability-detail");
      details.append(node("summary", "逐项结果与复跑方法"), node("p", workflow.method));
      details.append(table(["测试输入", "结果"], workflow.cases.map(row => [row.label, row.passed ? "通过" : "未通过"])));
      if (workflow.total_cells) details.append(node("p", `核对正确的数据格 ${workflow.cells}/${workflow.total_cells}。`));
      if (origin?.canonical_url) details.append(sourceLink(`复跑方法出处 · 第 ${workflow.source_line} 行`, `${origin.canonical_url}#L${workflow.source_line}`));
      item.append(details);
    }
    if (evidence) {
      const details = node("details", undefined, "capability-detail");
      details.append(node("summary", `声明依据 · ${evidence.path} 第 ${evidence.start_line} 行`));
      if (evidence.quote) details.append(node("pre", evidence.quote));
      if (origin?.canonical_url) details.append(sourceLink("打开原文位置", `${origin.canonical_url}#L${evidence.start_line}`));
      item.append(details);
    }
    list.append(item);
  }
  if (!profile.capabilities.length) list.append(node("p", "已读材料中暂未提取出具体能力条目。可在报告顶部查看原始用途。"));
  section.append(list);
  if (profile.documented_limits.length) {
    const limits = node("details", undefined, "capability-detail");
    limits.append(node("summary", "作者写明的限制与注意事项"));
    for (const ref of profile.documented_limits) evidenceLine(limits, ref);
    section.append(limits);
  }
  return section;
}

function documentationPanel(profile) {
  const {documentation} = profile;
  const section = node("details", undefined, "card");
  section.append(node("summary", `为什么说明覆盖是 ${documentation.found}/${documentation.total}`));
  const rows = documentation.checks.map(row => {
    if (!row.evidence) return [row.title, "本次规则未找到"];
    const details = node("details");
    details.append(node("summary", `第 ${row.evidence.start_line} 行`), node("pre", row.evidence.quote));
    return [row.title, details];
  });
  section.append(table(["检查项", "说明依据"], rows));
  return section;
}

function peerPanel(profile) {
  const comparison = profile.comparison;
  const section = panel("同类公开 Skill 对比", comparison.scope);
  section.id = "skill-comparison";
  if (!comparison.peers.length) {
    section.append(peerLinkForm(profile));
    return section;
  }

  const rows = [[`${profile.name}（当前）`, profile.size.document_characters,
    `${profile.documentation.found}/${profile.documentation.total}`, "当前评估版本"]];
  for (const peer of comparison.peers) {
    const evidence = node("div", undefined, "peer-evidence");
    if (peer.current_more.length) evidence.append(node("p", `当前说明另外覆盖 ${peer.current_more.join("、")}。`));
    if (peer.peer_more.length) evidence.append(node("p", `该同类说明另外覆盖 ${peer.peer_more.join("、")}。`));
    for (const row of peer.review) evidence.append(sourceLink(`${row.text}〔第 ${row.line} 行〕`, `${peer.url}#L${row.line}`));
    rows.push([sourceLink(peer.name, peer.url), peer.size, `${peer.documentation.found}/${peer.documentation.total}`, evidence]);
  }
  section.append(table(["Skill", "字符", "说明覆盖", "主要差异"], rows));
  if (comparison.method) {
    const results = [{name: `${profile.name}（当前）`, measurements: profile.measurements}, ...comparison.peers];
    section.append(node("h4", "同输入复跑"), table(["Skill", "逐页文字", "网格提表"], results.map(row => [
      row.name, ...["text", "table"].map(key => row.measurements?.[key] ? observationText(row.measurements[key]) : "暂无同条件记录"),
    ])), node("p", comparison.method, "muted"));
  }
  section.append(peerLinkForm(profile));
  return section;
}

function peerLinkForm(current) {
  const details = node("details", undefined, "capability-detail");
  details.append(node("summary", "添加一个公开 Skill 作对照"));
  const form = node("form", undefined, "input-row peer-form");
  const input = node("input");
  input.type = "url";
  input.required = true;
  input.maxLength = 2048;
  input.setAttribute("aria-label", "对照 Skill 链接");
  input.placeholder = "粘贴同用途 Skill 的 SKILL.md 链接";
  const button = node("button", "对照", "secondary");
  form.append(input, button);
  const results = node("div");
  results.setAttribute("role", "status");
  form.onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    button.disabled = true;
    input.readOnly = true;
    results.textContent = "正在评估对照链接…";
    try {
      const candidate = await request("/import-url", {url: input.value.trim()});
      if (candidate.state === "selection_required") {
        results.textContent = "仓库中有多个 Skill，请粘贴要比较的具体 SKILL.md 链接。";
        return;
      }
      const peer = candidate.report.skill_profile;
      const docs = profile => `${profile.documentation.found}/${profile.documentation.total}`;
      const rows = [
        ["说明字符", current.size.document_characters, peer.size.document_characters],
        ["说明覆盖", docs(current), docs(peer)],
        ...current.documentation.checks.map((check, i) => [
          check.title, check.found ? "已定位" : "本次规则未找到",
          peer.documentation.checks[i].found ? "已定位" : "本次规则未找到",
        ]),
      ];
      if (current.family === peer.family && current.family !== "general") {
        const documented = profile => new Map(profile.capabilities.filter(row => row.state === "declared").map(row => [row.id, row.title]));
        const left = documented(current);
        const right = documented(peer);
        for (const [id, title] of new Map([...left, ...right])) {
          rows.push([title, left.has(id) ? "说明有记录" : "本次规则未找到", right.has(id) ? "说明有记录" : "本次规则未找到"]);
          if (current.measurements?.[id] && peer.measurements?.[id]) rows.push([
            `${title}（同输入复跑）`, observationText(current.measurements[id]), observationText(peer.measurements[id]),
          ]);
        }
      }
      const origin = candidate.report.source_import;
      results.replaceChildren(table(["比较项", current.name, peer.name], rows),
        sourceLink("打开对照版本的原始说明", origin.canonical_url));
      current.comparison.requested_peer = {name: peer.name, url: origin.canonical_url, commit: origin.commit, rows};
    } catch (error) {
      results.textContent = error.message;
    } finally {
      setBusy(false);
      button.disabled = false;
      input.readOnly = false;
    }
  };
  input.oninput = () => {
    delete current.comparison.requested_peer;
    results.replaceChildren();
  };
  details.append(form, results);
  return details;
}

function adoptionPanel(proof) {
  const result = proof.result;
  const holdout = proof.holdout;
  const section = panel("历史配对实测", proof.holdout_observation_zh, "measurement");
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

function taskGuidance(guidance) {
  const section = panel(guidance.title, guidance.summary);
  section.id = "task-guidance";
  const checks = node("ul", undefined, "task-checks");
  for (const check of guidance.checks) {
    const line = node("li");
    line.append(node("strong", check.name), document.createTextNode(` · ${check.detail}`));
    checks.append(line);
  }
  section.append(checks);
  if (guidance.evidence.length) {
    const sources = node("details", undefined, "finding");
    sources.append(node("summary", "查看说明中的工具依据"));
    for (const reference of guidance.evidence) evidenceLine(sources, reference);
    section.append(sources);
  }
  if (guidance.probe_available) {
    const link = node("a", "用自己的 PDF 试跑");
    link.href = "#pdf-probe";
    link.onclick = () => { byId("pdf-probe").open = true; };
    section.append(link);
  }
  return section;
}

function renderReport(report) {
  const root = byId("report");
  const warning = ["repair_required", "configuration_required", "task_contract_review"].includes(report.decision);
  const inspection = panel(report.decision_zh, report.reason, `verdict${warning ? " warning" : ""}`);
  const profile = report.skill_profile;
  if (profile) {
    root.replaceChildren(profileOverview(profile, report.source_import));
    if (profile.public_benchmark) root.append(publishedEvidence(profile.public_benchmark));
    if (report.package.resource_links?.length) root.append(resourceEvaluation(report.package.resource_links, report.source_import));
    root.append(capacityPanel(profile, report.source_import), peerPanel(profile), documentationPanel(profile));
  } else {
    root.replaceChildren(inspection);
  }
  if (profile && warning) root.append(inspection);
  if (report.current_task?.state === "unverified") {
    const task = panel(report.current_task.title, report.current_task.reason, "current-task");
    task.id = "current-task-evidence";
    root.append(task);
  }
  if (report.task_guidance) root.append(taskGuidance(report.task_guidance));

  const proof = report.verified_adoption;
  if (proof?.state === "measured_on_registered_inputs") {
    if (report.current_task?.state === "unverified") {
      const history = node("details", undefined, "card");
      history.append(node("summary", "展开该版本的历史配对实测", "archive-title"), adoptionPanel(proof));
      root.append(history);
    } else {
      root.append(adoptionPanel(proof));
    }
  } else if (!profile) {
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
  const quick = byId("quick-result");
  const summary = report.quick_summary;
  if (summary) {
    const uses = node("section", undefined, "quick-answer");
    uses.append(node("h3", "能做什么"), node("p", summary.purpose));
    const quality = node("section", undefined, "quick-answer");
    quality.append(node("h3", "好不好用"), node("p", summary.verdict, `quick-verdict ${summary.tone}`), node("p", summary.reason));
    const detail = node("a", "查看详细报告");
    detail.href = "/";
    detail.dataset.viewLink = "detail";
    const footer = node("div", undefined, "quick-footer");
    footer.append(detail);
    const length = report.skill_profile.size.document_characters.toLocaleString("zh-CN");
    quick.replaceChildren(node("h2", summary.name), node("p", "说明 " + length + " 字符", "quick-length"),
      uses, quality, scoreCards(report.assessment), footer);
    quick.hidden = false;
  }
  latestReport = report;
  byId("export-report").disabled = false;
  byId("report-region").hidden = false;
  activeReport().focus();
}

function showCandidates(choices, revision, selected) {
  const select = node("select");
  select.setAttribute("aria-label", "选择要评估的 Skill");
  const placeholder = node("option", "选择一个 Skill");
  placeholder.value = "";
  select.append(placeholder);
  for (const item of choices.candidates) {
    const option = node("option", item.path.replace(/\/SKILL\.md$/, ""));
    option.value = item.path;
    select.append(option);
  }
  if (selected) select.value = choices.candidates.some(item => item.path === selected) ? selected : "";
  const button = node("button", "评估所选");
  button.type = "button";
  button.disabled = !select.value || select.value === selected;
  select.onchange = () => { button.disabled = !select.value || select.value === selected; };
  button.onclick = () => scanLink(select.value, revision, choices);
  const row = node("div", undefined, "input-row candidate-picker");
  row.append(select, button);
  const target = byId("candidates");
  if (selected) {
    const change = node("details");
    change.append(node("summary", "换一个 Skill"), row);
    target.append(change);
  } else {
    target.append(row);
    select.focus();
  }
  if (choices.candidates_truncated) {
    target.append(node("p", "这里只列出前 200 项。你也可以粘贴具体 Skill 的链接。", "muted"));
  }
}

async function scanLink(skillPath, revision, previousChoices) {
  if (busy) return;
  const url = byId("skill-url").value.trim();
  if (!url) return;
  invalidate();
  setBusy(true);
  status("link-status", "正在读取和核对版本…");
  try {
    const data = await request("/import-url", { url, skill_path: skillPath, revision, prefer_main: true, ...conditions() });
    if (data.state === "selection_required") {
      status("link-status", `这个链接包含 ${data.candidate_count} 个 Skill。选一个即可评估。`);
      showCandidates(data, data.source.commit);
      return;
    }
    renderReport(data.report);
    const choices = data.source.selection || previousChoices;
    if (choices) showCandidates(choices, data.source.commit, data.source.skill_path);
    status("link-status", data.source.selection ? "已评估仓库主 Skill。" : "评估完成。");
  } catch (error) {
    status("link-status", error.message, true);
    if (previousChoices) showCandidates(previousChoices, revision);
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
    byId("quick-example").disabled = false;
    byId("quick-example").onclick = registered.onclick;
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
byId("public-example").onclick = () => example("https://github.com/anthropics/skills/blob/e4bb5d960d87126ff3b3a8af1a104b7db67560ba/skills/pdf/SKILL.md");
byId("spreadsheet-example").onclick = () => example("https://github.com/anthropics/skills/blob/e4bb5d960d87126ff3b3a8af1a104b7db67560ba/skills/xlsx/SKILL.md");
byId("published-benchmark-example").onclick = () => example("https://github.com/NVIDIA/skills/blob/252ba53487b73eaafbfcccf3e750c7b350b93db9/skills/accelerated-computing-cudf/SKILL.md");

byId("inspect-file").onclick = async () => {
  if (busy) return;
  const file = byId("skill-file").files[0];
  if (!file) return status("file-status", "请选择 SKILL.md。", true);
  if (file.name.toLowerCase().endsWith(".pdf")) return status("file-status", "请从下方的 PDF 表格试跑上传这份文件。", true);
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
  downloadFile("skill-assessment.json", JSON.stringify(latestReport, null, 2), "application/json");
};

function downloadFile(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], {type}));
  const link = node("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function encodeFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
    reader.onerror = () => reject(new Error("无法读取所选 PDF。"));
    reader.readAsDataURL(file);
  });
}

function showPdfResult(result) {
  const target = byId("pdf-result");
  if (result.state !== "extracted") {
    const title = result.state === "needs_ocr" ? "需要 OCR" : "暂未得到可导出的表格";
    target.replaceChildren(panel(title, result.reason, "verdict warning"));
    return;
  }
  const checked = result.verification;
  const conflict = ["disagreement", "shape_mismatch"].includes(checked.state);
  const title = conflict ? "已提取表格，核对有差异" : "已提取表格";
  const box = panel(title, `第 ${result.page} 页 · ${result.method_label} · ${result.row_count} 行 × ${result.column_count} 列`,
    conflict ? "verdict warning" : "measurement");
  if (checked.state === "verified_match" || checked.state === "disagreement") {
    box.append(node("p", `参考 CSV 核对 ${checked.matched_rows}/${checked.reference_rows} 行、${checked.matched_cells}/${checked.reference_cells} 个数据格一致。`));
    if (checked.expected_rows != null) {
      box.append(node("p", `行数与指定值不同，预期 ${checked.expected_rows} 行，实际 ${checked.actual_rows} 行。`, "error"));
    }
    for (const diff of checked.differences) {
      box.append(node("p", `${diff.row} · ${diff.column} · 预期 ${diff.expected}，实际 ${diff.actual}`, "error"));
    }
    for (const [key, label] of [["missing_rows", "缺少"], ["unexpected_rows", "额外"], ["duplicate_rows", "重复"]]) {
      if (checked[key].length) box.append(node("p", `${label}行：${checked[key].join("、")}`, "error"));
    }
  } else if (checked.state === "shape_mismatch") {
    box.append(node("p", `行数不一致。预期 ${checked.expected_rows} 行，实际 ${checked.actual_rows} 行。`, "error"));
  } else {
    box.append(node("p", "参考 CSV 未提供，数值待核对。", "muted"));
  }
  box.append(table(result.header, result.preview));
  if (result.row_count > result.preview.length) box.append(node("p", `预览前 ${result.preview.length} 行，完整结果见 CSV。`, "muted"));
  box.append(node("p", "本次使用本地解析器验证这个文件。Skill 的有无配对收益仍待测量。", "muted"));
  const download = node("button", "下载 CSV", "secondary");
  download.type = "button";
  download.onclick = () => downloadFile(`pdf-table-page-${result.page}.csv`, `\uFEFF${result.csv}`, "text/csv;charset=utf-8");
  box.append(download);
  target.replaceChildren(box);
}

const pdfForm = byId("pdf-form");
pdfForm.oninput = () => { byId("pdf-result").replaceChildren(); status("pdf-status", ""); };
pdfForm.onsubmit = async event => {
  event.preventDefault();
  const button = byId("run-pdf");
  if (button.disabled) return;
  const pdf = byId("task-pdf").files[0];
  const reference = byId("pdf-reference").files[0];
  if (!pdf || pdf.size > 4_000_000) return status("pdf-status", "请选择不超过 4 MB 的 PDF。", true);
  if (reference?.size > 64_000) return status("pdf-status", "参考 CSV 需小于 64 KB。", true);
  const fields = [...pdfForm.querySelectorAll("input, button")];
  fields.forEach(field => { field.disabled = true; });
  byId("pdf-result").replaceChildren();
  status("pdf-status", "正在检查文字层、提取表格并核对…");
  try {
    const result = await request("/task/pdf-table", {
      pdf_base64: await encodeFile(pdf), page: Number(byId("pdf-page").value),
      headers: byId("pdf-headers").value, expected_rows: byId("pdf-rows").value ? Number(byId("pdf-rows").value) : null,
      reference_csv: reference ? await reference.text() : "",
    });
    showPdfResult(result);
    status("pdf-status", result.state === "extracted" ? "试跑完成。" : "已检查所选页。");
  } catch (error) {
    status("pdf-status", error.message, true);
  } finally {
    fields.forEach(field => { field.disabled = false; });
  }
};

loadStudies();
