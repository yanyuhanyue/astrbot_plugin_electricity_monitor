(() => {
  "use strict";

  let bridge = null;
  const state = {
    revision: 0,
    sessions: [],
    subscriptions: [],
    selectedUmo: "",
    editingRoom: null,
    quickHistoryToken: 0,
  };
  const $ = (selector) => document.querySelector(selector);

  function unwrap(response) {
    if (response?.status === "error" || response?.ok === false) {
      const error = new Error(response.message || "接口请求失败。");
      error.code = response.code;
      throw error;
    }
    return response?.data ?? response ?? {};
  }

  async function apiGet(endpoint) {
    return unwrap(await bridge.apiGet(endpoint));
  }

  async function apiPost(endpoint, body = {}) {
    return unwrap(await bridge.apiPost(endpoint, body));
  }

  function toast(message, type = "success") {
    const item = document.createElement("div");
    item.className = `toast ${type}`;
    item.textContent = message;
    $("#toastStack").append(item);
    setTimeout(() => item.remove(), 3600);
  }

  async function busy(button, work) {
    const previous = button.disabled;
    button.disabled = true;
    try {
      await work();
    } catch (error) {
      toast(error.message || String(error), "error");
      if (error.code === 409) await loadData();
    } finally {
      button.disabled = previous;
    }
  }

  function formatTime(timestamp) {
    return timestamp ? new Date(timestamp * 1000).toLocaleString() : "-";
  }

  function sessionLabel(session) {
    const type = session.chat_type === "group" ? "群聊" : "私聊";
    const id = session.session_id || session.umo;
    const name = String(session.display_name || "").trim();
    const synthetic = `${type} ${id}`;
    if (!name || name === synthetic) return `${type} · ${id}`;
    if (name.startsWith(`${type} `)) {
      return `${type} · ${name.slice(type.length + 1)}`;
    }
    return `${type} · ${name} (${id})`;
  }

  function selectedSubscriptions() {
    return sortedSubscriptions(
      state.subscriptions.filter((item) => item.umo === state.selectedUmo),
    );
  }

  function sessionByUmo(umo) {
    return state.sessions.find((item) => item.umo === umo);
  }

  function option(select, value, label) {
    const item = document.createElement("option");
    item.value = value;
    item.textContent = label;
    select.append(item);
  }

  function pill(text, className) {
    const item = document.createElement("span");
    item.className = className;
    item.textContent = text;
    return item;
  }

  function textCell(text) {
    const cell = document.createElement("td");
    cell.textContent = text;
    return cell;
  }

  function roundedRect(context, x, y, width, height, radius) {
    const r = Math.min(radius, width / 2, height / 2);
    context.beginPath();
    context.moveTo(x + r, y);
    context.lineTo(x + width - r, y);
    context.quadraticCurveTo(x + width, y, x + width, y + r);
    context.lineTo(x + width, y + height - r);
    context.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
    context.lineTo(x + r, y + height);
    context.quadraticCurveTo(x, y + height, x, y + height - r);
    context.lineTo(x, y + r);
    context.quadraticCurveTo(x, y, x + r, y);
    context.closePath();
  }

  function tokenColor(name, alpha = "1") {
    const value = getComputedStyle(document.documentElement)
      .getPropertyValue(name)
      .trim();
    return value ? `hsl(${value} / ${alpha})` : `hsl(240 5.9% 10% / ${alpha})`;
  }

  function fillSelect(select, items, placeholder) {
    select.innerHTML = "";
    option(select, "", placeholder);
    for (const item of items) option(select, item.code, item.name);
  }

  function fillSelectWithSelection(select, items, placeholder, value, label) {
    fillSelect(select, items, placeholder);
    if (value && !items.some((item) => String(item.code) === String(value))) {
      option(select, value, label || value);
    }
    select.value = value || "";
  }

  function selectedName(select) {
    return select.selectedOptions[0]?.textContent || "";
  }

  function numericValue(value) {
    if (value == null || value === "") return Number.POSITIVE_INFINITY;
    const number = Number(value);
    return Number.isFinite(number) ? number : Number.POSITIVE_INFINITY;
  }

  function sortedSubscriptions(items) {
    return [...items].sort((a, b) => {
      const aValue = numericValue(a.latest_value);
      const bValue = numericValue(b.latest_value);
      if (aValue !== bValue) return aValue - bValue;
      if (Boolean(a.last_error) !== Boolean(b.last_error)) {
        return a.last_error ? 1 : -1;
      }
      return String(a.alias).localeCompare(String(b.alias), "zh-CN");
    });
  }

  function bestSessionUmo() {
    if (!state.subscriptions.length) return state.sessions[0]?.umo || "";
    const ranked = sortedSubscriptions(state.subscriptions);
    return ranked[0]?.umo || state.sessions[0]?.umo || "";
  }

  function renderSessions() {
    const select = $("#sessionSelect");
    select.innerHTML = "";
    for (const session of state.sessions) {
      option(select, session.umo, sessionLabel(session));
    }
    const validSelection = state.sessions.some((item) => item.umo === state.selectedUmo);
    const selectedHasSubscriptions = state.subscriptions.some((item) => item.umo === state.selectedUmo);
    if (!validSelection || !selectedHasSubscriptions) {
      state.selectedUmo = bestSessionUmo();
    }
    select.value = state.selectedUmo;
    renderSubscriptions();
  }

  function renderCopyOptions() {
    const select = $("#copySubscriptionSelect");
    const button = $("#copySubscriptionButton");
    const current = select.value;
    const items = sortedSubscriptions(
      state.subscriptions.filter((item) => item.umo !== state.selectedUmo),
    );
    select.innerHTML = "";
    option(select, "", items.length ? "选择要复制的订阅" : "暂无可复制订阅");
    for (const item of items) {
      const session = sessionByUmo(item.umo);
      option(
        select,
        item.id,
        `${item.alias} · ${session ? sessionLabel(session) : item.umo}`,
      );
    }
    if ([...select.options].some((item) => item.value === current)) {
      select.value = current;
    }
    button.disabled = !state.selectedUmo || !items.length;
  }

  function renderSubscriptions() {
    const body = $("#subscriptionBody");
    body.innerHTML = "";
    const rows = selectedSubscriptions();
    $("#currentSessionBadge").textContent = rows.length;
    if (!rows.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 5;
      cell.className = "empty";
      cell.textContent = "当前会话尚未配置寝室。";
      row.append(cell);
      body.append(row);
    }
    for (const item of rows) {
      const row = document.createElement("tr");
      const balance = item.latest_balance ? `\n余额 ${item.latest_balance}` : "";
      const latest = item.latest_value == null
        ? (item.last_error ? `查询失败\n${item.last_error}` : "等待首次查询")
        : `${item.latest_value} ${item.unit}${balance}\n${formatTime(item.latest_at)}`;
      const status = item.enabled ? (item.last_error ? "异常" : "监控中") : "已停用";
      row.append(
        textCell(`${item.alias}\n${item.room.room_name || item.room.room_code}`),
        textCell(latest),
        textCell(`${item.threshold} ${item.unit}\n${item.interval_seconds / 60} 分钟`),
      );
      const statusCell = document.createElement("td");
      const statusClass = item.enabled ? (item.last_error ? "error" : "ok") : "muted";
      statusCell.append(pill(status, `status-pill ${statusClass}`));
      if (item.alerted) {
        const note = document.createElement("div");
        note.className = "status-note";
        note.textContent = "已触发低电量提醒";
        statusCell.append(note);
      }
      row.append(statusCell);
      const actions = document.createElement("td");
      const edit = document.createElement("button");
      edit.className = "mini";
      edit.textContent = "编辑";
      edit.addEventListener("click", () => editSubscription(item));
      actions.append(edit);
      row.append(actions);
      body.append(row);
    }
    renderHistoryOptions();
    renderCopyOptions();
    void renderQuickHistory(rows);
  }

  function focusEditor() {
    document.querySelector(".editor")?.scrollIntoView({ behavior: "smooth", block: "start" });
    $("#alias").focus();
  }

  function resetEditor() {
    $("#editorTitle").textContent = "新增寝室";
    $("#subscriptionId").value = "";
    $("#alias").value = "";
    $("#threshold").value = "20";
    $("#unit").value = "度";
    $("#intervalMinutes").value = "15";
    $("#enabled").checked = true;
    $("#manualRoomEnabled").checked = false;
    for (const id of [
      "#manualAreaId", "#manualBuildingCode", "#manualFloorCode",
      "#manualRoomCode", "#manualRoomName",
    ]) {
      $(id).value = "";
    }
    state.editingRoom = null;
    ["#areaSelect", "#buildingSelect", "#floorSelect", "#roomSelect"].forEach((id) => {
      fillSelect($(id), [], "请先加载");
    });
    $("#roomHint").textContent = "请点击“加载校区”并逐级选择寝室。";
    $("#manualDetails").open = false;
    $("#deleteSubscriptionButton").disabled = true;
    $("#querySubscriptionButton").disabled = true;
    $("#queryResult").textContent = "尚未查询。";
    $("#queryResult").className = "result empty";
    updateRoomInputMode();
  }

  function editSubscription(item) {
    $("#editorTitle").textContent = `编辑：${item.alias}`;
    $("#subscriptionId").value = item.id;
    $("#alias").value = item.alias;
    $("#threshold").value = item.threshold;
    $("#unit").value = item.unit;
    $("#intervalMinutes").value = item.interval_seconds / 60;
    $("#enabled").checked = item.enabled;
    $("#manualRoomEnabled").checked = false;
    $("#manualAreaId").value = item.room.area_id;
    $("#manualBuildingCode").value = item.room.building_code;
    $("#manualFloorCode").value = item.room.floor_code;
    $("#manualRoomCode").value = item.room.room_code;
    $("#manualRoomName").value = item.room.room_name || "";
    state.editingRoom = item.room;
    fillSelect($("#areaSelect"), [{ code: item.room.area_id, name: item.room.area_name || item.room.area_id }], "校区");
    fillSelect($("#buildingSelect"), [{ code: item.room.building_code, name: item.room.building_name || item.room.building_code }], "楼栋");
    fillSelect($("#floorSelect"), [{ code: item.room.floor_code, name: item.room.floor_name || item.room.floor_code }], "楼层");
    fillSelect($("#roomSelect"), [{ code: item.room.room_code, name: item.room.room_name || item.room.room_code }], "房间");
    $("#areaSelect").value = item.room.area_id;
    $("#buildingSelect").value = item.room.building_code;
    $("#floorSelect").value = item.room.floor_code;
    $("#roomSelect").value = item.room.room_code;
    $("#roomHint").textContent = item.room.display_name || [
      item.room.area_name, item.room.building_name, item.room.floor_name, item.room.room_name,
    ].filter(Boolean).join(" / ");
    $("#manualDetails").open = false;
    $("#deleteSubscriptionButton").disabled = false;
    $("#querySubscriptionButton").disabled = false;
    updateRoomInputMode();
    void refreshLocationChoices(item.room);
  }

  async function refreshLocationChoices(room) {
    if (!room || $("#manualRoomEnabled").checked) return;
    try {
      const areas = await apiPost("locations/areas");
      fillSelectWithSelection(
        $("#areaSelect"),
        areas.items,
        "请选择校区",
        room.area_id,
        room.area_name || room.area_id,
      );
      const buildings = await apiPost("locations/buildings", { area_id: room.area_id });
      fillSelectWithSelection(
        $("#buildingSelect"),
        buildings.items,
        "请选择楼栋",
        room.building_code,
        room.building_name || room.building_code,
      );
      const floors = await apiPost("locations/floors", {
        area_id: room.area_id,
        building_code: room.building_code,
      });
      fillSelectWithSelection(
        $("#floorSelect"),
        floors.items,
        "请选择楼层",
        room.floor_code,
        room.floor_name || room.floor_code,
      );
      const rooms = await apiPost("locations/rooms", {
        area_id: room.area_id,
        building_code: room.building_code,
        floor_code: room.floor_code,
      });
      fillSelectWithSelection(
        $("#roomSelect"),
        rooms.items,
        "请选择房间",
        room.room_code,
        room.room_name || room.room_code,
      );
      $("#roomHint").textContent = "已加载当前位置的同级选项，可重新选择楼层或房间。";
    } catch (error) {
      $("#roomHint").textContent = "当前位置已保留；同级选项加载失败，可点击“加载校区”重新选择。";
      toast(error.message || String(error), "error");
    }
  }

  function updateRoomInputMode() {
    const manual = $("#manualRoomEnabled").checked;
    if (manual) $("#manualDetails").open = true;
    for (const id of [
      "#manualAreaId", "#manualBuildingCode", "#manualFloorCode",
      "#manualRoomCode", "#manualRoomName",
    ]) {
      $(id).disabled = !manual;
    }
    for (const id of [
      "#areaSelect", "#buildingSelect", "#floorSelect", "#roomSelect",
      "#loadAreasButton",
    ]) {
      $(id).disabled = manual;
    }
  }

  function currentRoom() {
    if ($("#manualRoomEnabled").checked) {
      const values = {
        area_id: $("#manualAreaId").value.trim(),
        building_code: $("#manualBuildingCode").value.trim(),
        floor_code: $("#manualFloorCode").value.trim(),
        room_code: $("#manualRoomCode").value.trim(),
      };
      const missing = Object.entries(values)
        .filter(([, value]) => !value)
        .map(([key]) => key);
      if (missing.length) {
        throw new Error(`手动房间参数不完整：${missing.join("、")}。`);
      }
      return {
        ...values,
        area_name: "",
        building_name: "",
        floor_name: "",
        room_name: $("#manualRoomName").value.trim()
          || $("#alias").value.trim()
          || values.room_code,
      };
    }
    const area = $("#areaSelect");
    const building = $("#buildingSelect");
    const floor = $("#floorSelect");
    const room = $("#roomSelect");
    if (!area.value || !building.value || !floor.value || !room.value) {
      if (state.editingRoom) return state.editingRoom;
      throw new Error("请完整选择校区、楼栋、楼层和房间。");
    }
    return {
      area_id: area.value,
      area_name: selectedName(area),
      building_code: building.value,
      building_name: selectedName(building),
      floor_code: floor.value,
      floor_name: selectedName(floor),
      room_code: room.value,
      room_name: selectedName(room),
    };
  }

  function readConfig() {
    const alias = $("#alias").value.trim();
    const threshold = $("#threshold").value.trim();
    const unit = $("#unit").value.trim();
    const interval = Number($("#intervalMinutes").value);
    if (!alias) throw new Error("请填写订阅别名。");
    if (!threshold || !Number.isFinite(Number(threshold))) throw new Error("阈值不是有效数字。");
    if (!unit) throw new Error("请填写单位。");
    if (!Number.isInteger(interval) || interval < 5 || interval > 1440) {
      throw new Error("查询频率必须是 5–1440 分钟的整数。");
    }
    return {
      alias,
      threshold,
      unit,
      interval_seconds: interval * 60,
      enabled: $("#enabled").checked,
    };
  }

  async function loadAreas() {
    const data = await apiPost("locations/areas");
    fillSelect($("#areaSelect"), data.items, "请选择校区");
    fillSelect($("#buildingSelect"), [], "请先选择校区");
    fillSelect($("#floorSelect"), [], "请先选择楼栋");
    fillSelect($("#roomSelect"), [], "请先选择楼层");
    state.editingRoom = null;
    $("#roomHint").textContent = `已读取 ${data.items.length} 个校区。`;
  }

  async function loadBuildings() {
    if (!$("#areaSelect").value) return;
    state.editingRoom = null;
    const data = await apiPost("locations/buildings", { area_id: $("#areaSelect").value });
    fillSelect(
      $("#buildingSelect"),
      data.items,
      data.items.length ? "请选择楼栋" : "该校区未返回楼栋",
    );
    fillSelect($("#floorSelect"), [], "请先选择楼栋");
    fillSelect($("#roomSelect"), [], "请先选择楼层");
    $("#roomHint").textContent = data.items.length
      ? `已读取 ${data.items.length} 栋楼。`
      : "该校区未返回楼栋。请刷新校区后重选，或使用下方手动接口参数。";
  }

  async function loadFloors() {
    if (!$("#buildingSelect").value) return;
    state.editingRoom = null;
    const data = await apiPost("locations/floors", {
      area_id: $("#areaSelect").value,
      building_code: $("#buildingSelect").value,
    });
    fillSelect(
      $("#floorSelect"),
      data.items,
      data.items.length ? "请选择楼层" : "该楼栋未返回楼层",
    );
    fillSelect($("#roomSelect"), [], "请先选择楼层");
    $("#roomHint").textContent = data.items.length
      ? `已读取 ${data.items.length} 个楼层。`
      : "该楼栋未返回楼层，可改用手动接口参数。";
  }

  async function loadRooms() {
    if (!$("#floorSelect").value) return;
    state.editingRoom = null;
    const data = await apiPost("locations/rooms", {
      area_id: $("#areaSelect").value,
      building_code: $("#buildingSelect").value,
      floor_code: $("#floorSelect").value,
    });
    fillSelect(
      $("#roomSelect"),
      data.items,
      data.items.length ? "请选择房间" : "该楼层未返回房间",
    );
    $("#roomHint").textContent = data.items.length
      ? `已读取 ${data.items.length} 个房间。`
      : "该楼层未返回房间，可改用手动接口参数。";
  }

  function renderCredentialStatus(credentials) {
    const labels = {
      valid: "有效",
      expired: "已过期，后台查询已暂停",
      unknown: "已配置，尚未验证",
      unconfigured: "未配置",
    };
    const mask = credentials.configured
      ? [
        `shiroJID ${credentials.shiro_jid_masked}`,
        credentials.ym_id_configured
          ? `ymId ${credentials.ym_id_masked}`
          : "ymId 未填写（可选）",
      ].join("，")
      : "未保存凭据";
    const status = String(credentials.state || "unknown");
    const statusEl = $("#credentialStatus");
    statusEl.className = `hint credential-state ${status}`;
    statusEl.textContent = `状态：${labels[status] || status}。${mask}${credentials.error ? `。${credentials.error}` : ""}`;
  }

  function uniqueAlias(base) {
    const used = new Set(selectedSubscriptions().map((item) => item.alias.toLowerCase()));
    if (!used.has(base.toLowerCase())) return base;
    for (let index = 2; index < 100; index += 1) {
      const candidate = `${base} ${index}`;
      if (!used.has(candidate.toLowerCase())) return candidate;
    }
    return `${base} ${Date.now()}`;
  }

  function renderAdminTargets(adminNoticeUmo) {
    const select = $("#adminNoticeSelect");
    select.innerHTML = "";
    option(select, "", "不发送私信通知");
    for (const session of state.sessions.filter((item) => item.chat_type === "private")) {
      option(select, session.umo, sessionLabel(session));
    }
    select.value = adminNoticeUmo || "";
  }

  function renderHistoryOptions() {
    const select = $("#historySubscriptionSelect");
    const current = select.value;
    select.innerHTML = "";
    for (const item of state.subscriptions) {
      const session = state.sessions.find((candidate) => candidate.umo === item.umo);
      option(select, item.id, `${item.alias} · ${session ? sessionLabel(session) : item.umo}`);
    }
    if ([...select.options].some((item) => item.value === current)) select.value = current;
  }

  function switchTab(name) {
    document.querySelectorAll(".tab").forEach(
      (item) => item.classList.toggle("active", item.dataset.tab === name),
    );
    document.querySelectorAll(".panel").forEach(
      (panel) => panel.classList.toggle("active", panel.id === `panel-${name}`),
    );
  }

  async function renderQuickHistory(rows) {
    const summary = $("#quickHistorySummary");
    const title = $("#quickHistoryTitle");
    const caption = $("#quickHistoryCaption");
    const token = state.quickHistoryToken + 1;
    state.quickHistoryToken = token;
    const target = rows.find((item) => item.latest_value != null) || rows[0];
    if (!target) {
      title.textContent = "近30天电量趋势";
      caption.textContent = "优先展示当前会话电量最低的寝室。";
      summary.textContent = "当前会话暂无可展示趋势。";
      drawHistoryOnCanvas("#quickHistoryChart", [], "度", { compact: true });
      return;
    }
    title.textContent = `近30天电量趋势（${target.alias}）`;
    caption.textContent = "数据参考最近 30 天采样。";
    summary.textContent = "正在加载趋势。";
    try {
      const result = await apiPost("history", { subscription_id: target.id });
      if (token !== state.quickHistoryToken) return;
      drawHistoryOnCanvas("#quickHistoryChart", result.items, result.subscription.unit, { compact: true });
      const values = result.items.map((item) => Number(item.value));
      summary.textContent = values.length
        ? `共 ${values.length} 个采样，最低 ${Math.min(...values)} ${result.subscription.unit}，最高 ${Math.max(...values)} ${result.subscription.unit}。`
        : "最近 30 天暂无采样。";
    } catch (error) {
      if (token !== state.quickHistoryToken) return;
      drawHistoryOnCanvas("#quickHistoryChart", [], target.unit || "度", { compact: true });
      summary.textContent = error.message || String(error);
    }
  }

  function renderDiagnostics(items) {
    const body = $("#diagnosticBody");
    body.innerHTML = "";
    if (!items.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.className = "empty";
      cell.textContent = "暂无诊断。";
      row.append(cell);
      body.append(row);
      return;
    }
    for (const item of items) {
      const row = document.createElement("tr");
      row.append(textCell(formatTime(item.created_at)));
      const scopeCell = document.createElement("td");
      scopeCell.append(pill(item.scope, "scope-pill"));
      row.append(scopeCell);
      const level = String(item.level || "info").toLowerCase();
      const levelCell = document.createElement("td");
      levelCell.append(pill(item.level, `level-pill ${level}`));
      row.append(levelCell);
      row.append(textCell(item.message));
      body.append(row);
    }
  }

  async function loadData() {
    $("#runtimeStatus").textContent = "正在读取";
    const data = await apiGet("bootstrap");
    state.revision = data.revision;
    state.sessions = data.sessions;
    state.subscriptions = data.subscriptions;
    $("#sessionCount").textContent = data.sessions.length;
    $("#subscriptionCount").textContent = data.subscriptions.length;
    $("#activeCount").textContent = data.subscriptions.filter((item) => item.enabled).length;
    $("#diagnosticCount").textContent = data.diagnostics.length;
    renderSessions();
    renderCredentialStatus(data.credentials);
    renderAdminTargets(data.admin_notice_umo);
    renderDiagnostics(data.diagnostics);
    $("#runtimeStatus").textContent = "运行中";
    $("#runtimeStatus").className = "status ok";
  }

  function drawHistoryOnCanvas(selector, items, unit, options = {}) {
    const compact = Boolean(options.compact);
    const canvas = $(selector);
    const context = canvas.getContext("2d");
    const width = canvas.width;
    const height = canvas.height;
    const latest = compact ? null : $("#historyLatest");
    const primary = tokenColor("--primary");
    const primarySoft = tokenColor("--primary", "0.10");
    const border = tokenColor("--border");
    const muted = tokenColor("--muted-foreground");
    const background = tokenColor("--background");
    context.clearRect(0, 0, width, height);
    context.fillStyle = background;
    context.fillRect(0, 0, width, height);
    if (!items.length) {
      if (latest) {
        latest.textContent = "未加载";
        latest.className = "latest-chip empty";
      }
      context.fillStyle = muted;
      context.font = "16px sans-serif";
      context.fillText("暂无历史采样", 30, 50);
      return;
    }
    const values = items.map((item) => Number(item.value));
    const min = Math.min(...values);
    const max = Math.max(...values);
    const rawSpan = Math.max(max - min, 0);
    const padding = Math.max(rawSpan * 0.12, max > 100 ? 8 : 1);
    const floor = Math.max(0, min - padding);
    const ceiling = max + padding;
    const span = Math.max(ceiling - floor, 1);
    const left = compact ? 60 : 70;
    const right = compact ? width - 34 : width - 70;
    const top = compact ? 22 : 34;
    const bottom = compact ? height - 42 : height - 58;
    const points = items.map((item, index) => ({
      x: left + ((right - left) * index) / Math.max(items.length - 1, 1),
      y: bottom - ((Number(item.value) - floor) / span) * (bottom - top),
      item,
    }));
    context.strokeStyle = border;
    context.lineWidth = 1;
    context.setLineDash([4, 5]);
    for (let index = 0; index <= 4; index += 1) {
      const y = top + ((bottom - top) * index) / 4;
      context.beginPath();
      context.moveTo(left, y);
      context.lineTo(right, y);
      context.stroke();
    }
    context.setLineDash([]);
    context.beginPath();
    points.forEach((point, index) => {
      if (index === 0) context.moveTo(point.x, point.y);
      else context.lineTo(point.x, point.y);
    });
    context.lineTo(points[points.length - 1].x, bottom);
    context.lineTo(points[0].x, bottom);
    context.closePath();
    context.fillStyle = primarySoft;
    context.fill();
    context.strokeStyle = primary;
    context.lineWidth = 3;
    context.lineJoin = "round";
    context.lineCap = "round";
    context.beginPath();
    points.forEach((point, index) => {
      if (index === 0) context.moveTo(point.x, point.y);
      else context.lineTo(point.x, point.y);
    });
    context.stroke();
    context.fillStyle = background;
    context.strokeStyle = primary;
    context.lineWidth = 3;
    for (const point of [points[0], points[points.length - 1]]) {
      context.beginPath();
      context.arc(point.x, point.y, 6, 0, Math.PI * 2);
      context.fill();
      context.stroke();
    }
    const lastPoint = points[points.length - 1];
    const lastValue = Number(lastPoint.item.value);
    const label = `${lastValue.toFixed(2)} ${unit}`;
    if (latest) {
      latest.textContent = `最新值 ${label}`;
      latest.className = "latest-chip";
    }
    context.font = "bold 14px sans-serif";
    const labelWidth = context.measureText(label).width + 22;
    const labelX = Math.min(right - labelWidth, lastPoint.x + 12);
    const labelY = Math.max(top + 4, lastPoint.y - 17);
    context.fillStyle = primary;
    roundedRect(context, labelX, labelY, labelWidth, 32, 6);
    context.fill();
    context.fillStyle = tokenColor("--primary-foreground");
    context.fillText(label, labelX + 11, labelY + 21);
    context.fillStyle = muted;
    context.font = "13px sans-serif";
    context.fillText(`${ceiling.toFixed(2)} ${unit}`, 6, top + 5);
    context.fillText(`${floor.toFixed(2)} ${unit}`, 6, bottom + 5);
    context.fillText(formatTime(items[0].captured_at), left, height - 14);
    const endText = formatTime(items[items.length - 1].captured_at);
    context.fillText(endText, Math.max(left, right - context.measureText(endText).width), height - 14);
  }

  function drawHistory(items, unit) {
    drawHistoryOnCanvas("#historyChart", items, unit);
  }

  document.querySelectorAll(".tab").forEach((button) => {
    button.addEventListener("click", () => {
      switchTab(button.dataset.tab);
    });
  });

  $("#sessionSelect").addEventListener("change", (event) => {
    state.selectedUmo = event.target.value;
    renderSubscriptions();
    resetEditor();
  });
  $("#refreshButton").addEventListener("click", () => busy($("#refreshButton"), loadData));
  $("#importSessionsButton").addEventListener("click", () => busy($("#importSessionsButton"), async () => {
    const result = await apiPost("sessions/import");
    toast(result.message);
    await loadData();
  }));
  $("#newSubscriptionButton").addEventListener("click", () => {
    resetEditor();
    focusEditor();
    toast("已切换到新增寝室，填写别名后选择寝室即可保存。");
  });
  $("#quickHistoryButton").addEventListener("click", () => {
    const rows = selectedSubscriptions();
    const target = rows.find((item) => item.latest_value != null) || rows[0];
    if (target) $("#historySubscriptionSelect").value = String(target.id);
    switchTab("history");
  });
  $("#copySubscriptionButton").addEventListener("click", () => busy($("#copySubscriptionButton"), async () => {
    if (!state.selectedUmo) throw new Error("请先选择目标会话。");
    const source = state.subscriptions.find(
      (item) => String(item.id) === String($("#copySubscriptionSelect").value),
    );
    if (!source) throw new Error("请选择要复制的订阅。");
    const result = await apiPost("subscriptions/save", {
      revision: state.revision,
      umo: state.selectedUmo,
      subscription_id: null,
      room: source.room,
      config: {
        alias: uniqueAlias(source.alias),
        threshold: source.threshold,
        unit: source.unit,
        interval_seconds: source.interval_seconds,
        enabled: source.enabled,
      },
    });
    const errors = result.report?.errors || [];
    toast(result.message, errors.length ? "error" : "success");
    await loadData();
    editSubscription(result.subscription);
    focusEditor();
  }));
  $("#manualRoomEnabled").addEventListener("change", updateRoomInputMode);
  $("#loadAreasButton").addEventListener("click", () => busy($("#loadAreasButton"), loadAreas));
  $("#areaSelect").addEventListener("change", () => {
    state.editingRoom = null;
    busy($("#areaSelect"), loadBuildings);
  });
  $("#buildingSelect").addEventListener("change", () => {
    state.editingRoom = null;
    busy($("#buildingSelect"), loadFloors);
  });
  $("#floorSelect").addEventListener("change", () => {
    state.editingRoom = null;
    busy($("#floorSelect"), loadRooms);
  });
  $("#roomSelect").addEventListener("change", () => {
    state.editingRoom = null;
    $("#roomHint").textContent = [
      selectedName($("#areaSelect")), selectedName($("#buildingSelect")),
      selectedName($("#floorSelect")), selectedName($("#roomSelect")),
    ].filter(Boolean).join(" / ");
  });
  $("#saveSubscriptionButton").addEventListener("click", () => busy($("#saveSubscriptionButton"), async () => {
    if (!state.selectedUmo) throw new Error("请先选择会话。");
    const result = await apiPost("subscriptions/save", {
      revision: state.revision,
      umo: state.selectedUmo,
      subscription_id: $("#subscriptionId").value || null,
      room: currentRoom(),
      config: readConfig(),
    });
    const item = result.report?.items?.[0];
    const errors = result.report?.errors || [];
    $("#queryResult").className = item ? "result" : "result empty";
    $("#queryResult").textContent = item
      ? `${item.alias}：${item.value} ${item.unit}${item.balance ? `\n余额：${item.balance}` : ""}\n${item.room_name}\n${formatTime(item.captured_at)}`
      : errors.join("；") || "订阅已保存，首次查询未取得数据。";
    toast(result.message, errors.length ? "error" : "success");
    await loadData();
    editSubscription(result.subscription);
  }));
  $("#deleteSubscriptionButton").addEventListener("click", () => busy($("#deleteSubscriptionButton"), async () => {
    const result = await apiPost("subscriptions/delete", {
      revision: state.revision,
      umo: state.selectedUmo,
      subscription_id: $("#subscriptionId").value,
    });
    toast(result.message);
    resetEditor();
    await loadData();
  }));
  $("#querySubscriptionButton").addEventListener("click", () => busy($("#querySubscriptionButton"), async () => {
    const result = await apiPost("query/run", { subscription_id: $("#subscriptionId").value });
    const item = result.report.items[0];
    $("#queryResult").className = "result";
    $("#queryResult").textContent = item
      ? `${item.alias}：${item.value} ${item.unit}${item.balance ? `\n余额：${item.balance}` : ""}\n${item.room_name}\n${formatTime(item.captured_at)}`
      : result.report.errors.join("；") || "没有取得数据。";
    toast(result.message);
    await loadData();
  }));
  $("#saveCredentialsButton").addEventListener("click", () => busy($("#saveCredentialsButton"), async () => {
    const result = await apiPost("credentials/save", {
      shiroJID: $("#shiroJID").value,
      ymId: $("#ymId").value,
    });
    $("#shiroJID").value = "";
    $("#ymId").value = "";
    toast(result.message);
    await loadData();
  }));
  $("#verifyCredentialsButton").addEventListener("click", () => busy($("#verifyCredentialsButton"), async () => {
    const result = await apiPost("credentials/verify");
    toast(result.message);
    await loadData();
  }));
  $("#clearCredentialsButton").addEventListener("click", () => busy($("#clearCredentialsButton"), async () => {
    const result = await apiPost("credentials/clear");
    toast(result.message);
    await loadData();
  }));
  $("#saveAdminNoticeButton").addEventListener("click", () => busy($("#saveAdminNoticeButton"), async () => {
    const result = await apiPost("settings/admin-notice", { umo: $("#adminNoticeSelect").value });
    toast(result.message);
    await loadData();
  }));
  $("#testNotificationButton").addEventListener("click", () => busy($("#testNotificationButton"), async () => {
    const result = await apiPost("notification/test");
    toast(result.message);
  }));
  $("#loadHistoryButton").addEventListener("click", () => busy($("#loadHistoryButton"), async () => {
    const subscriptionId = $("#historySubscriptionSelect").value;
    if (!subscriptionId) throw new Error("暂无可查看的寝室订阅。");
    const result = await apiPost("history", { subscription_id: subscriptionId });
    drawHistory(result.items, result.subscription.unit);
    const values = result.items.map((item) => Number(item.value));
    $("#historySummary").textContent = values.length
      ? `共 ${values.length} 个采样，最低 ${Math.min(...values)} ${result.subscription.unit}，最高 ${Math.max(...values)} ${result.subscription.unit}。`
      : "最近 30 天暂无采样。";
  }));

  async function waitForBridge() {
    for (let attempt = 0; attempt < 50; attempt += 1) {
      if (window.AstrBotPluginPage?.ready) return window.AstrBotPluginPage;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error("AstrBot 插件页面桥接器加载超时。");
  }

  async function initialize() {
    try {
      bridge = await waitForBridge();
      await bridge.ready();
      resetEditor();
      await loadData();
    } catch (error) {
      $("#runtimeStatus").textContent = "连接失败";
      $("#runtimeStatus").className = "status error";
      toast(error.message || String(error), "error");
    }
  }

  initialize();
})();
