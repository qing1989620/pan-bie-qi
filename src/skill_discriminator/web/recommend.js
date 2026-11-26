const element = id => document.getElementById(id);
const endpoint = "/api/discriminator";

function dom(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function anchor(text, href) {
  const link = dom("a", text);
  link.href = href;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

function skillCard(item) {
  const card = dom("article", undefined, "card pt-card");
  card.dataset.condition = item.fit;
  const header = dom("header", undefined, "pt-card-top");
  const condition = item.fit === "conditional" ? "采用前先核对" : item.role === "alternative" ? "可比较的备选" : "覆盖所需能力";
  header.append(dom("h3", item.name), dom("span", condition, "pt-grade"));
  card.append(header, dom("p", item.source.repository + " · " + item.license.id, "muted pt-small"));
  const matches = item.matches.map(match => match.label).join("、");
  card.append(dom("p", "推荐理由 · " + matches, "pt-reason"), dom("p", item.purpose));
  if (item.missing.length) {
    card.append(dom("p", "还需另外核对 " + item.missing.map(row => row.label).join("、") + "。", "pt-gap"));
  }
  if (item.conditions_to_check.length) {
    card.append(dom("p", item.conditions_to_check.join("。") + "。", "pt-gap"));
  }
  const assessment = item.assessment;
  card.append(dom("p", "判别器结论 · " + assessment.verdict + "。" + assessment.reason, "pt-proof"));
  const proof = assessment.task_evidence;
  if (proof.score !== null) {
    const tested = dom("p", proof.basis + " " + proof.score + " / 100。" + proof.reason, "pt-proof");
    if (proof.source_url) tested.append(" ", anchor("原评测", proof.source_url));
    card.append(tested);
  }
  const links = dom("div", undefined, "pt-card-links");
  links.append(anchor("查看完整评估", "/user?url=" + encodeURIComponent(item.source.canonical_url)),
    anchor("开源说明", item.source.canonical_url));
  if (item.license.materials.length) links.append(anchor("许可证", item.license.materials[0].url));
  card.append(links);
  const evidence = dom("details");
  evidence.append(dom("summary", "查看匹配和评分依据"));
  const list = dom("ul");
  for (const match of item.matches) {
    const row = dom("li");
    const source = match.evidence;
    row.append(anchor(match.label + " · 第 " + source.start_line + " 行", source.url),
      dom("blockquote", source.quote));
    list.append(row);
  }
  evidence.append(list, dom("p", "说明 " + assessment.characters.toLocaleString("zh-CN") + " 字符。判别器读取 " +
    assessment.files_analyzed + "/" + assessment.inventory_files + " 个文件。提交 " +
    item.source.commit.slice(0, 12) + "。", "muted pt-small"));
  const scores = dom("ul");
  for (const dimension of assessment.dimensions) {
    const score = dimension.score === null ? dimension.basis : dimension.score + " / 100（" + dimension.basis + "）";
    scores.append(dom("li", dimension.label + " · " + score + "。" + dimension.reason));
  }
  evidence.append(scores);
  if (proof.scope) evidence.append(dom("p", proof.scope, "muted pt-small"));
  card.append(evidence);
  return card;
}

function render(report) {
  const output = element("pt-result");
  const items = report.recommendations;
  const cover = dom("section", undefined, "pt-coverage");
  cover.append(dom("h2", items.length ? "推荐结果" : "本次没有足够依据的推荐"));
  const {discovery, assessment, model} = report.pipeline;
  const succeeded = discovery.searched.filter(row => row.state === "searched").length;
  const listing = discovery.provider + " " + succeeded + "/" + discovery.searched.length + " 次查询成功。处理 " +
    assessment.candidates.length + " 个候选，返回 " + items.length + " 个。";
  cover.append(dom("p", listing, "muted pt-small"));
  if (report.requirements.length) {
    const list = dom("ul");
    for (const row of report.requirements) {
      list.append(dom("li", row.label + (row.covered ? " · 有说明依据" : " · 未找到合适候选"),
        row.covered ? "" : "pt-gap"));
    }
    cover.append(list);
  }
  if (model.state === "used") cover.append(dom("p", "DeepSeek 已补充搜索词。", "muted pt-small"));
  if (!["used", "not_requested"].includes(model.state)) {
    cover.append(dom("p", "DeepSeek 本次未提供可用搜索词，已继续使用本地搜索词。", "muted pt-small"));
  }
  output.replaceChildren(cover, ...items.map(skillCard));
  const filtered = assessment.candidates.filter(row => row.reason);
  if (filtered.length) {
    const trace = dom("details", undefined, "card pt-trace");
    trace.append(dom("summary", "查看筛除的候选（" + filtered.length + "）"));
    const list = dom("ul");
    for (const row of filtered) {
      const entry = dom("li");
      entry.append(row.url ? anchor(row.key, row.url) : document.createTextNode(row.key), " · " + row.reason);
      for (const evidence of row.evidence || []) {
        entry.append(" ", anchor(evidence.path + " 第 " + evidence.start_line + " 行", evidence.url));
      }
      list.append(entry);
    }
    trace.append(list);
    output.append(trace);
  }
  output.hidden = false;
  output.focus();
}

const form = element("pt-form");
const task = element("pt-task");
const submit = element("pt-submit");
const status = element("pt-status");
for (const example of form.querySelectorAll("[data-example]")) {
  example.onclick = () => { task.value = example.dataset.example; task.focus(); };
}

form.onsubmit = async event => {
  event.preventDefault();
  if (submit.disabled) return;
  const network = element("pt-network").value;
  const body = {task: task.value.trim(), limit: 3, use_model: element("pt-model").checked,
    environment: {network_allowed: network ? network === "yes" : null}};
  submit.disabled = true;
  status.classList.remove("error");
  status.textContent = "正在搜索公开目录并核查候选 Skill…";
  element("pt-result").hidden = true;
  try {
    const response = await fetch(endpoint + "/recommend", {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) {
      const error = Array.isArray(data.detail) ? data.detail.map(item => item.msg).join("，") : data.detail;
      throw new Error(error || "本次请求未完成。");
    }
    render(data);
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message || "本次请求未完成。";
    status.classList.add("error");
  } finally {
    submit.disabled = false;
  }
};

fetch(endpoint + "/health").then(response => response.json()).then(info => {
  if (info.deepseek_search_planning_configured) element("pt-model").disabled = false;
  else element("pt-model-note").textContent = "当前使用本地搜索词。";
}).catch(() => {});
