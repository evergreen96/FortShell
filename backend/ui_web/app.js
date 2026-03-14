const state = {
  target: ".",
};

function $(id) {
  return document.getElementById(id);
}

function badge(label, className = "") {
  const element = document.createElement("span");
  element.className = `badge ${className}`.trim();
  element.textContent = label;
  return element;
}

function setBanner(message) {
  const banner = $("status-banner");
  if (!message) {
    banner.hidden = true;
    banner.textContent = "";
    return;
  }
  banner.hidden = false;
  banner.textContent = message;
}

function renderSummary(containerId, rows) {
  const container = $(containerId);
  container.replaceChildren();
  rows.forEach(([label, value]) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    container.append(dt, dd);
  });
}

function renderWorkspace(entries) {
  const list = $("workspace-tree");
  const empty = $("workspace-empty");
  const count = $("entry-count");
  const template = $("tree-item-template");

  list.replaceChildren();
  count.textContent = `${entries.length} visible entries`;
  empty.hidden = entries.length > 0;
  if (!entries.length) {
    return;
  }

  entries.forEach((entry) => {
    const fragment = template.content.cloneNode(true);
    fragment.querySelector(".tree-path").textContent = entry.display_path || entry.path;
    const meta = fragment.querySelector(".tree-meta");
    meta.appendChild(badge(entry.is_dir ? "Directory" : "File"));
    const button = fragment.querySelector(".tree-action");
    button.disabled = !entry.suggested_deny_rule;
    button.textContent = entry.suggested_deny_rule ? "Hide from AI" : "No rule";
    button.addEventListener("click", () => mutatePolicy("/api/policy/deny", entry.suggested_deny_rule));
    list.appendChild(fragment);
  });
}

function renderRules(rules) {
  const list = $("policy-rules");
  const empty = $("policy-empty");
  const template = $("rule-item-template");
  list.replaceChildren();
  empty.hidden = rules.length > 0;
  if (!rules.length) {
    return;
  }

  rules.forEach((rule) => {
    const fragment = template.content.cloneNode(true);
    fragment.querySelector(".rule-value").textContent = rule;
    fragment.querySelector(".rule-action").addEventListener("click", () => mutatePolicy("/api/policy/allow", rule));
    list.appendChild(fragment);
  });
}

function renderSnapshot(snapshot) {
  state.target = snapshot.target || ".";
  $("target-input").value = state.target;
  renderSummary("session-summary", [
    ["Execution", snapshot.session.execution_session_id || "-"],
    ["Agent", snapshot.session.agent_session_id || "-"],
  ]);
  renderSummary("policy-summary", [
    ["Version", String(snapshot.policy.version)],
    ["Rules", String(snapshot.policy.deny_globs.length)],
  ]);
  renderSummary("index-summary", [
    ["Policy version", String(snapshot.workspace_index.policy_version)],
    ["Stale", snapshot.workspace_index.stale ? "yes" : "no"],
    ["Entries", String(snapshot.workspace_index.entry_count)],
    ["Files", String(snapshot.workspace_index.file_count)],
    ["Directories", String(snapshot.workspace_index.directory_count)],
  ]);

  const staleReasons = snapshot.workspace_index.stale_reasons || [];
  setBanner(staleReasons.length ? `Workspace index stale: ${staleReasons.join(", ")}` : "");

  renderWorkspace(snapshot.workspace.entries || []);
  renderRules(snapshot.policy.deny_globs || []);
}

async function readJson(response) {
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function loadSnapshot(target = state.target) {
  const response = await fetch(`/api/workspace-panel?target=${encodeURIComponent(target)}`);
  const payload = await readJson(response);
  renderSnapshot(payload);
}

async function mutatePolicy(path, rule) {
  if (!rule) {
    return;
  }
  const response = await fetch(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      rule,
      target: state.target,
    }),
  });
  const payload = await readJson(response);
  renderSnapshot(payload.panel);
}

async function initialize() {
  $("target-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await loadSnapshot($("target-input").value.trim() || ".");
    } catch (error) {
      setBanner(error.message);
    }
  });

  try {
    await loadSnapshot(".");
  } catch (error) {
    setBanner(error.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  void initialize();
});
