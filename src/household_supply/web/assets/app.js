"use strict";

const state = {
  catalog: { items: [], skus: [] },
  household: null,
  reports: [],
  plans: [],
  history: [],
  activePlan: null,
  activeContext: null,
  mustHaves: new Map(),
  view: "shopping",
};

class ApiError extends Error {
  constructor(status, payload) {
    super(payload?.detail || payload?.error || `Request failed (${status})`);
    this.status = status;
    this.payload = payload;
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body
      ? { "content-type": "application/json", ...(options.headers || {}) }
      : (options.headers || {}),
  });
  const payload = await response.json().catch(() => ({ error: "invalid_response" }));
  if (!response.ok) throw new ApiError(response.status, payload);
  return payload;
}

function byId(id) { return document.getElementById(id); }
function itemById(id) { return state.catalog.items.find((item) => item.item_id === id); }
function skusForItem(id) { return state.catalog.skus.filter((sku) => sku.item_id === id); }
function primarySku(id) { return skusForItem(id)[0] || null; }
function itemName(id) { return itemById(id)?.name || id; }
function balanceForItem(id) { return (state.household?.balances || []).find((entry) => entry.item_id === id) || null; }
function reportForItem(id) { return state.reports.find((entry) => entry.item_id === id) || null; }

function decimalText(value) {
  const raw = String(value);
  const match = raw.match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
  if (!match) return raw;
  const sign = match[1] === "-" ? "-" : "";
  const integer = match[2];
  const fraction = match[3] || "";
  const exponent = Number(match[4] || 0);
  const digits = integer + fraction;
  const point = integer.length + exponent;
  let plain;
  if (point <= 0) plain = `0.${"0".repeat(-point)}${digits}`;
  else if (point >= digits.length) plain = `${digits}${"0".repeat(point - digits.length)}`;
  else plain = `${digits.slice(0, point)}.${digits.slice(point)}`;
  if (plain.includes(".")) plain = plain.replace(/0+$/, "").replace(/\.$/, "");
  plain = plain.replace(/^0+(?=\d)/, "");
  if (plain.startsWith(".")) plain = `0${plain}`;
  if (!plain || plain === "-0") plain = "0";
  return sign && plain !== "0" ? `${sign}${plain}` : plain;
}

function scaleDecimalText(value, numerator, denominator = 1) {
  let plain = decimalText(value);
  let sign = "";
  if (plain.startsWith("-")) {
    sign = "-";
    plain = plain.slice(1);
  }
  const [integer, fraction = ""] = plain.split(".");
  let scale = fraction.length;
  let scaled = BigInt(`${integer || "0"}${fraction}` || "0") * BigInt(numerator);
  const divisor = BigInt(denominator);
  while (scaled % divisor !== 0n) {
    scaled *= 10n;
    scale += 1;
  }
  const quotient = (scaled / divisor).toString().padStart(scale + 1, "0");
  const result = scale === 0
    ? quotient
    : `${quotient.slice(0, -scale) || "0"}.${quotient.slice(-scale)}`;
  return `${sign}${decimalText(result)}`;
}

function normalizeNumberInput(value) {
  return String(value).trim().replace(/[\s\u00a0]+/g, "").replace(",", ".");
}

function displayNumber(value) {
  const plain = decimalText(value);
  const numeric = Number(plain);
  if (Number.isFinite(numeric) && Math.abs(numeric) < 1e15) {
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 }).format(numeric);
  }
  return plain.replace(".", ",");
}

function unitLabel(unit) {
  const labels = {
    ml: "мл",
    l: "л",
    g: "г",
    kg: "кг",
    pcs: "шт.",
    piece: "шт.",
    pieces: "шт.",
    unit: "шт.",
  };
  return labels[unit] || unit;
}

function humanQuantity(quantity) {
  if (!quantity) return "—";
  let amount = Number(decimalText(quantity.amount));
  let unit = quantity.unit;
  if (Number.isFinite(amount)) {
    if (unit === "ml" && Math.abs(amount) >= 1000) {
      amount /= 1000;
      unit = "l";
    } else if (unit === "g" && Math.abs(amount) >= 1000) {
      amount /= 1000;
      unit = "kg";
    }
    return `${new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 3 }).format(amount)} ${unitLabel(unit)}`;
  }
  return `${displayNumber(quantity.amount)} ${unitLabel(unit)}`;
}

function moneyText(money) {
  if (!money) return "—";
  const currency = money.currency === "KGS" ? "сом" : money.currency;
  return `${displayNumber(money.amount)} ${currency}`;
}

function dayText(value) {
  const days = Number(value);
  if (!Number.isInteger(days)) return `${displayNumber(value)} дня`;
  const mod100 = days % 100;
  const mod10 = days % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${days} дней`;
  if (mod10 === 1) return `${days} день`;
  if (mod10 >= 2 && mod10 <= 4) return `${days} дня`;
  return `${days} дней`;
}

function packageText(count) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return `${count} упаковок`;
  if (mod10 === 1) return `${count} упаковка`;
  if (mod10 >= 2 && mod10 <= 4) return `${count} упаковки`;
  return `${count} упаковок`;
}

function itemEmoji(itemId) {
  const known = { milk: "🥛", rice: "🍚", oil: "🫗", sunflower_oil: "🫗", pasta: "🍝", semolina: "🥣", canned_fish: "🐟", canned_peas: "🫛", seasoning: "🧂", eggs: "🥚", bread: "🍞", sugar: "🧊" };
  if (known[itemId]) return known[itemId];
  const category = itemById(itemId)?.category || "";
  if (category.includes("dairy")) return "🥛";
  if (category.includes("pantry") || category.includes("grain")) return "🥫";
  return "🧺";
}

function eventId(prefix) {
  const random = crypto.getRandomValues(new Uint32Array(1))[0].toString(36);
  return `${prefix}-${Date.now().toString(36)}-${random}`.toLowerCase();
}

function formEventId(form, prefix) {
  if (!form.dataset.pendingEventId) form.dataset.pendingEventId = eventId(prefix);
  return form.dataset.pendingEventId;
}

function clearFormEventId(form) {
  delete form.dataset.pendingEventId;
}

function elementEventId(element, prefix, operationKey) {
  if (element.dataset.pendingOperationKey !== operationKey) {
    element.dataset.pendingOperationKey = operationKey;
    element.dataset.pendingEventId = eventId(prefix);
  }
  return element.dataset.pendingEventId;
}

function clearElementEventId(element) {
  delete element.dataset.pendingOperationKey;
  delete element.dataset.pendingEventId;
}

function friendlyError(error) {
  if (!(error instanceof ApiError)) return error?.message || String(error);
  const code = error.payload?.error;
  if (code === "market_unavailable") return "Не получилось получить актуальные цены. Попробуйте ещё раз.";
  if (code === "household_state_conflict") return "В данных о запасах есть противоречие. Обновите остаток ещё раз.";
  if (code === "household_operation_conflict") return "Это изменение уже было записано по-другому. Обновите страницу и попробуйте снова.";
  if (code === "not_found") return "Не нашли нужную запись. Обновите страницу и попробуйте снова.";
  if (code === "storage_error" || code === "household_storage_error") return "Не удалось сохранить данные. Попробуйте ещё раз.";
  if (code === "invalid_request") {
    const detail = String(error.payload?.detail || "").toLowerCase();
    if (detail.includes("requires explicit") || detail.includes("recorded consumption")) {
      return "Пока недостаточно данных. Отметьте, что есть дома, или добавьте продукт в «Нужно обязательно».";
    }
    return "Проверьте введённые данные и попробуйте ещё раз.";
  }
  return "Что-то пошло не так. Попробуйте ещё раз.";
}

function showToast(message, error = false) {
  const toast = byId("toast");
  toast.textContent = message;
  toast.classList.toggle("error", error);
  toast.classList.remove("hidden");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.add("hidden"), 4200);
}

function setConnection(online, text) {
  const node = byId("connection-status");
  node.classList.toggle("online", online);
  node.classList.toggle("offline", !online);
  node.lastElementChild.textContent = text;
}

function setView(name) {
  state.view = name;
  for (const button of document.querySelectorAll("[data-view]")) {
    button.classList.toggle("active", button.dataset.view === name);
  }
  for (const panel of document.querySelectorAll("[data-view-panel]")) {
    panel.classList.toggle("active", panel.dataset.viewPanel === name);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function appendOption(select, value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  select.appendChild(option);
}

function refillSkuSelect(select) {
  const current = select.value;
  select.replaceChildren();
  for (const sku of state.catalog.skus) {
    appendOption(select, sku.sku_id, `${sku.name} · ${humanQuantity(sku.package_quantity)}`);
  }
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function renderFirstRun() {
  byId("first-run-card").classList.toggle("hidden", state.history.length > 0);
}

function renderProductPicker() {
  const picker = byId("product-picker");
  const query = byId("product-search").value.trim().toLocaleLowerCase("ru-RU");
  picker.replaceChildren();
  const items = state.catalog.items.filter((item) => {
    const text = `${item.name} ${(item.aliases || []).join(" ")}`.toLocaleLowerCase("ru-RU");
    return !query || text.includes(query);
  });

  if (!items.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Ничего не нашли.";
    picker.appendChild(empty);
    return;
  }

  for (const item of items) {
    const sku = primarySku(item.item_id);
    if (!sku) continue;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "product-option";
    const emoji = document.createElement("span");
    emoji.className = "product-emoji";
    emoji.textContent = itemEmoji(item.item_id);
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = item.name;
    const note = document.createElement("small");
    note.textContent = `${humanQuantity(sku.package_quantity)} · добавить`;
    copy.append(title, note);
    button.append(emoji, copy);
    button.addEventListener("click", () => {
      state.mustHaves.set(item.item_id, (state.mustHaves.get(item.item_id) || 0) + 1);
      renderMustHaves();
    });
    picker.appendChild(button);
  }
}

function renderMustHaves() {
  const list = byId("must-have-list");
  list.replaceChildren();
  for (const [itemId, count] of [...state.mustHaves.entries()].sort()) {
    const sku = primarySku(itemId);
    if (!sku) continue;
    const row = document.createElement("div");
    row.className = "need-choice";

    const copy = document.createElement("div");
    const title = document.createElement("div");
    title.className = "need-choice-title";
    const emoji = document.createElement("span");
    emoji.textContent = itemEmoji(itemId);
    const name = document.createElement("strong");
    name.textContent = itemName(itemId);
    title.append(emoji, name);
    const note = document.createElement("small");
    note.textContent = `По ${humanQuantity(sku.package_quantity)} в упаковке`;
    copy.append(title, note);

    const stepper = makeStepper(count, (next) => {
      if (next <= 0) state.mustHaves.delete(itemId);
      else state.mustHaves.set(itemId, next);
      renderMustHaves();
    });

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button danger";
    remove.textContent = "Убрать";
    remove.addEventListener("click", () => {
      state.mustHaves.delete(itemId);
      renderMustHaves();
    });

    row.append(copy, stepper, remove);
    list.appendChild(row);
  }
}

function makeStepper(value, onChange, minimum = 0) {
  const stepper = document.createElement("div");
  stepper.className = "stepper";
  const minus = document.createElement("button");
  minus.type = "button";
  minus.className = "stepper-button";
  minus.textContent = "−";
  minus.setAttribute("aria-label", "Уменьшить");
  const current = document.createElement("span");
  current.className = "stepper-value";
  current.textContent = String(value);
  const plus = document.createElement("button");
  plus.type = "button";
  plus.className = "stepper-button";
  plus.textContent = "+";
  plus.setAttribute("aria-label", "Увеличить");
  minus.addEventListener("click", () => onChange(Math.max(minimum, value - 1)));
  plus.addEventListener("click", () => onChange(value + 1));
  stepper.append(minus, current, plus);
  return stepper;
}

function collectMustHaves() {
  const needs = [];
  for (const [itemId, count] of state.mustHaves.entries()) {
    const sku = primarySku(itemId);
    if (!sku || count <= 0) continue;
    needs.push({
      item_id: itemId,
      quantity: {
        amount: scaleDecimalText(sku.package_quantity.amount, count),
        unit: sku.package_quantity.unit,
      },
    });
  }
  return needs;
}

function renderHome() {
  const container = byId("home-items");
  container.replaceChildren();
  for (const item of state.catalog.items) {
    const sku = primarySku(item.item_id);
    if (!sku) continue;
    const balance = balanceForItem(item.item_id);
    const report = reportForItem(item.item_id);

    const card = document.createElement("article");
    card.className = "home-card";
    const top = document.createElement("div");
    top.className = "home-card-top";

    const titleBlock = document.createElement("div");
    titleBlock.className = "home-card-title";
    const emoji = document.createElement("span");
    emoji.className = "product-emoji";
    emoji.textContent = itemEmoji(item.item_id);
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = item.name;
    const pack = document.createElement("small");
    pack.textContent = `Одна упаковка: ${humanQuantity(sku.package_quantity)}`;
    copy.append(name, pack);
    titleBlock.append(emoji, copy);

    const current = document.createElement("div");
    current.className = "home-current";
    current.textContent = balance
      ? (Number(balance.quantity.amount) === 0 ? "Нет дома" : humanQuantity(balance.quantity))
      : "Пока не отмечено";
    top.append(titleBlock, current);
    card.appendChild(top);

    const hint = document.createElement("p");
    hint.className = "home-learning-hint";
    if (report?.estimate) {
      hint.textContent = `Обычно заканчивается примерно по ${humanQuantity(report.estimate.daily_quantity)} в день.`;
    } else if (balance) {
      hint.textContent = "Обновите остаток позже ещё раз — и система начнёт понимать, как быстро это заканчивается.";
    } else {
      hint.textContent = "Выберите примерно, сколько сейчас осталось.";
    }
    card.appendChild(hint);

    const choices = document.createElement("div");
    choices.className = "quick-stocktake";
    const countUnit = ["pcs", "piece", "pieces", "unit"].includes(sku.package_quantity.unit);
    const presets = countUnit
      ? [
          ["Нет", 0, 1],
          ["1 упаковка", 1, 1],
          ["2 упаковки", 2, 1],
          ["5 упаковок", 5, 1],
        ]
      : [
          ["Нет", 0, 1],
          ["Половина", 1, 2],
          ["1 упаковка", 1, 1],
          ["2 упаковки", 2, 1],
        ];

    for (const [label, numerator, denominator] of presets) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "stock-choice";
      button.textContent = label;
      button.addEventListener("click", async () => {
        const amount = scaleDecimalText(sku.package_quantity.amount, numerator, denominator);
        await saveStocktake(item.item_id, { amount, unit: sku.package_quantity.unit }, button);
      });
      choices.appendChild(button);
    }

    const other = document.createElement("button");
    other.type = "button";
    other.className = "stock-choice";
    other.textContent = "Другое";
    choices.appendChild(other);
    card.appendChild(choices);

    const custom = document.createElement("form");
    custom.className = "custom-stocktake";
    const label = document.createElement("label");
    const labelText = document.createElement("span");
    labelText.textContent = "Сколько осталось";
    const input = document.createElement("input");
    input.inputMode = "decimal";
    input.autocomplete = "off";
    input.placeholder = "например, 0,7";
    input.required = true;
    label.append(labelText, input);
    const unit = document.createElement("span");
    unit.className = "custom-unit";
    unit.textContent = unitLabel(sku.package_quantity.unit);
    const save = document.createElement("button");
    save.type = "submit";
    save.className = "secondary-button";
    save.textContent = "Сохранить";
    custom.append(label, unit, save);
    other.addEventListener("click", () => {
      custom.classList.toggle("visible");
      if (custom.classList.contains("visible")) input.focus();
    });
    custom.addEventListener("submit", async (event) => {
      event.preventDefault();
      const amount = normalizeNumberInput(input.value);
      if (!amount) return;
      await saveStocktake(item.item_id, { amount, unit: sku.package_quantity.unit }, save, custom);
      input.value = "";
      custom.classList.remove("visible");
    });
    card.appendChild(custom);
    container.appendChild(card);
  }
}

async function saveStocktake(itemId, quantity, trigger, form = null) {
  const operationKey = `${itemId}:${quantity.amount}:${quantity.unit}`;
  const eventIdentifier = form
    ? formEventId(form, "stocktake")
    : elementEventId(trigger, "stocktake", operationKey);
  try {
    trigger.disabled = true;
    await request("/household/stocktakes", {
      method: "POST",
      body: JSON.stringify({
        event_id: eventIdentifier,
        item_id: itemId,
        quantity,
        reason: "browser stock update",
      }),
    });
    if (form) clearFormEventId(form);
    else clearElementEventId(trigger);
    showToast(`${itemName(itemId)}: остаток обновлён.`);
    await refreshOperationalState();
  } catch (error) {
    showToast(friendlyError(error), true);
  } finally {
    trigger.disabled = false;
  }
}

function renderLearning() {
  const list = byId("learning-list");
  list.replaceChildren();
  if (!state.reports.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Пока мало данных. Просто обновляйте остатки время от времени.";
    list.appendChild(empty);
    return;
  }
  for (const report of state.reports) {
    const card = document.createElement("article");
    card.className = "stack-card";
    const row = document.createElement("div");
    row.className = "stack-card-row";
    const name = document.createElement("strong");
    name.textContent = `${itemEmoji(report.item_id)} ${itemName(report.item_id)}`;
    const rate = document.createElement("span");
    rate.textContent = report.estimate ? `${humanQuantity(report.estimate.daily_quantity)} / день` : "Нужно больше данных";
    row.append(name, rate);
    const note = document.createElement("small");
    note.textContent = report.estimate
      ? `Учтены наблюдения примерно за ${displayNumber(report.estimate.observed_days)} дн.`
      : "После следующего обновления остатка система попробует оценить расход.";
    card.append(row, note);
    list.appendChild(card);
  }
}

function renderRecentPlans() {
  const list = byId("recent-plans");
  list.replaceChildren();
  if (!state.plans.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Здесь появятся составленные списки покупок.";
    list.appendChild(empty);
    return;
  }
  for (const plan of state.plans) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "stack-card plan-history-button";
    const row = document.createElement("div");
    row.className = "stack-card-row";
    const date = document.createElement("strong");
    date.textContent = new Date(plan.created_at).toLocaleDateString("ru-RU", { day: "numeric", month: "long" });
    const cost = document.createElement("span");
    cost.textContent = moneyText(plan.total_cost);
    row.append(date, cost);
    const note = document.createElement("small");
    note.textContent = plan.status === "feasible" ? "Список был составлен" : "Не удалось подобрать покупки";
    button.append(row, note);
    button.addEventListener("click", async () => {
      await openStoredPlan(plan.plan_id);
      setView("shopping");
    });
    list.appendChild(button);
  }
}

function eventDescription(event) {
  const body = event.body || {};
  if (event.event_type === "inventory_correction") {
    return `${itemName(event.item.id)}: осталось ${humanQuantity(body.quantity_on_hand)}`;
  }
  if (event.event_type === "purchase") {
    return `Купили ${itemName(event.item.id)} — ${humanQuantity(body.quantity)}`;
  }
  if (event.event_type === "consumption_observation") {
    return `${itemName(event.item.id)}: учтён расход ${humanQuantity(body.quantity_consumed)}`;
  }
  return itemName(event.item?.id || event.event_id);
}

function renderActivity() {
  const list = byId("activity-list");
  list.replaceChildren();
  const events = [...state.history].reverse().slice(0, 16);
  if (!events.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Пока ничего не менялось.";
    list.appendChild(empty);
    return;
  }
  for (const event of events) {
    const row = document.createElement("div");
    row.className = "activity-row";
    const time = document.createElement("span");
    time.className = "activity-time";
    time.textContent = new Date(event.recorded_at).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
    const description = document.createElement("strong");
    description.className = "activity-description";
    description.textContent = eventDescription(event);
    row.append(time, description);
    list.appendChild(row);
  }
}

function addChip(container, label, value) {
  const chip = document.createElement("span");
  chip.className = "summary-chip";
  chip.append(document.createTextNode(label));
  const strong = document.createElement("strong");
  strong.textContent = value;
  chip.appendChild(strong);
  container.appendChild(chip);
}

function buildCoverageReason(record, context) {
  const result = record.result || {};
  const requestData = record.request || {};
  const demands = new Map((requestData.demands || []).map((entry) => [entry.item_id, entry.quantity]));
  const nodes = [];
  for (const coverage of result.coverage || []) {
    const item = document.createElement("article");
    item.className = "explanation-item";
    const title = document.createElement("strong");
    title.textContent = `${itemEmoji(coverage.item_id)} ${itemName(coverage.item_id)}`;
    const detail = document.createElement("small");
    const demand = demands.get(coverage.item_id) || coverage.required;
    detail.textContent = `Нужно ${humanQuantity(demand)} · дома учтено ${humanQuantity(coverage.inventory_used)} · покупкой добавим ${humanQuantity(coverage.purchased)}.`;
    item.append(title, detail);
    nodes.push(item);
  }
  if (!nodes.length && result.status !== "feasible") {
    const item = document.createElement("article");
    item.className = "explanation-item";
    const title = document.createElement("strong");
    title.textContent = "Не удалось подобрать набор покупок";
    const detail = document.createElement("small");
    detail.textContent = result.minimum_required_cost
      ? `Для выполнения всех потребностей нужно минимум около ${moneyText(result.minimum_required_cost)}.`
      : "Попробуйте увеличить бюджет или изменить обязательные продукты.";
    item.append(title, detail);
    nodes.push(item);
  }
  return nodes;
}

function renderPlan(record, context = null) {
  state.activePlan = record;
  state.activeContext = context;
  const panel = byId("plan-result-panel");
  panel.classList.remove("hidden");

  const result = record.result || {};
  const feasible = result.status === "feasible";
  const status = byId("plan-status");
  status.textContent = feasible ? "Можно купить" : "Нужно изменить";
  status.classList.toggle("infeasible", !feasible);

  const summary = byId("plan-summary");
  summary.replaceChildren();
  addChip(summary, "Итого", moneyText(result.total_cost));
  addChip(summary, "Останется", moneyText(result.budget_remaining));
  if (context?.demand?.horizon_days) addChip(summary, "На", dayText(context.demand.horizon_days));

  const purchases = byId("purchase-list");
  purchases.replaceChildren();
  if (!(result.purchases || []).length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = feasible
      ? "Похоже, сейчас ничего докупать не нужно."
      : "С этим бюджетом не получилось подобрать все нужные покупки.";
    purchases.appendChild(empty);
  }

  for (const purchase of result.purchases || []) {
    const card = document.createElement("article");
    card.className = "purchase-card";
    const main = document.createElement("div");
    main.className = "purchase-main";
    const left = document.createElement("div");
    left.className = "purchase-left";
    const emoji = document.createElement("span");
    emoji.className = "product-emoji";
    emoji.textContent = itemEmoji(purchase.item_id);
    const copy = document.createElement("div");
    const title = document.createElement("div");
    title.className = "purchase-title";
    title.textContent = purchase.sku_name || itemName(purchase.item_id);
    const meta = document.createElement("div");
    meta.className = "purchase-meta";
    meta.textContent = `${packageText(purchase.packs)} · всего ${humanQuantity(purchase.acquired_quantity)}`;
    copy.append(title, meta);
    left.append(emoji, copy);
    const cost = document.createElement("div");
    cost.className = "purchase-cost";
    cost.textContent = moneyText(purchase.cost);
    main.append(left, cost);
    card.appendChild(main);

    const confirmation = document.createElement("div");
    confirmation.className = "confirm-row";
    const question = document.createElement("span");
    question.className = "confirm-question";
    question.textContent = "После магазина отметьте, сколько купили";
    const controls = document.createElement("div");
    controls.className = "confirm-controls";
    let actualPacks = purchase.packs;
    const stepperHolder = document.createElement("div");
    const redrawStepper = () => {
      stepperHolder.replaceChildren(makeStepper(actualPacks, (next) => {
        actualPacks = Math.max(1, next);
        redrawStepper();
      }, 1));
    };
    redrawStepper();
    const confirm = document.createElement("button");
    confirm.type = "button";
    confirm.className = "secondary-button";
    confirm.textContent = "Купил(а)";
    confirm.addEventListener("click", async () => {
      try {
        confirm.disabled = true;
        const operationKey = `hsp:purchase:${record.plan_id}:${purchase.offer_id || purchase.sku_id}`;
        let operationId = sessionStorage.getItem(operationKey);
        if (!operationId) {
          operationId = eventId("purchase");
          sessionStorage.setItem(operationKey, operationId);
        }
        const response = await request(`/plans/${encodeURIComponent(record.plan_id)}/purchases`, {
          method: "POST",
          body: JSON.stringify({ event_id: operationId, sku_id: purchase.sku_id, packs: actualPacks }),
        });
        sessionStorage.removeItem(operationKey);
        confirmation.replaceChildren();
        const done = document.createElement("span");
        done.className = "confirmed";
        done.textContent = `✓ Отмечено: ${packageText(response.purchase?.actual_packs ?? actualPacks)}`;
        confirmation.appendChild(done);
        showToast("Покупка добавлена в домашние запасы.");
        await refreshOperationalState();
      } catch (error) {
        showToast(friendlyError(error), true);
        confirm.disabled = false;
      }
    });
    controls.append(stepperHolder, confirm);
    confirmation.append(question, controls);
    card.appendChild(confirmation);
    purchases.appendChild(card);
  }

  const explanation = byId("explanation-list");
  explanation.replaceChildren(...buildCoverageReason(record, context));
  panel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function openStoredPlan(planId) {
  try {
    const record = await request(`/plans/${encodeURIComponent(planId)}`);
    renderPlan(record, null);
  } catch (error) {
    showToast(friendlyError(error), true);
  }
}

async function refreshOperationalState() {
  const [household, estimates, history, plans] = await Promise.all([
    request("/household/state"),
    request("/household/estimates"),
    request("/household/history"),
    request("/plans?limit=12"),
  ]);
  state.household = household.household;
  state.reports = estimates.reports || [];
  state.history = history.events || [];
  state.plans = plans.plans || [];
  renderFirstRun();
  renderHome();
  renderLearning();
  renderRecentPlans();
  renderActivity();
}

async function refreshAll() {
  try {
    setConnection(false, "Подключаемся…");
    const catalog = await request("/catalog");
    state.catalog = catalog.catalog;
    refillSkuSelect(byId("manual-purchase-sku"));
    renderProductPicker();
    renderMustHaves();
    await refreshOperationalState();
    setConnection(true, "Работает");
  } catch (error) {
    setConnection(false, "Нет связи");
    showToast(friendlyError(error), true);
  }
}

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => setView(button.dataset.view));
}

byId("start-home-setup").addEventListener("click", () => setView("home"));
byId("product-search").addEventListener("input", renderProductPicker);

for (const button of document.querySelectorAll("[data-days]")) {
  button.addEventListener("click", () => {
    byId("plan-horizon").value = button.dataset.days;
    for (const choice of document.querySelectorAll("[data-days]")) {
      choice.classList.toggle("selected", choice === button);
    }
  });
}

for (const button of document.querySelectorAll("[data-step-target]")) {
  button.addEventListener("click", () => {
    const input = byId(button.dataset.stepTarget);
    const current = Number(input.value) || 1;
    const step = Number(button.dataset.step);
    input.value = String(Math.max(1, current + step));
  });
}

byId("manual-purchase-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  try {
    button.disabled = true;
    const packs = Number(byId("manual-purchase-packs").value);
    if (!Number.isInteger(packs) || packs < 1) throw new Error("Количество упаковок должно быть целым числом.");
    const form = byId("manual-purchase-form");
    await request("/household/purchases", {
      method: "POST",
      body: JSON.stringify({
        event_id: formEventId(form, "purchase"),
        sku_id: byId("manual-purchase-sku").value,
        packs,
      }),
    });
    clearFormEventId(form);
    showToast("Покупка добавлена в домашние запасы.");
    await refreshOperationalState();
  } catch (error) {
    showToast(friendlyError(error), true);
  } finally {
    button.disabled = false;
  }
});

byId("plan-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = byId("build-plan-button");
  try {
    button.disabled = true;
    const budget = normalizeNumberInput(byId("plan-budget").value);
    if (!budget) throw new Error("Укажите бюджет.");
    const payload = {
      budget: { amount: budget, currency: byId("plan-currency").value },
      horizon_days: byId("plan-horizon").value,
      explicit_needs: collectMustHaves(),
    };
    const response = await request("/plans", { method: "POST", body: JSON.stringify(payload) });
    renderPlan(response.plan, response.household || null);
    showToast("Покупки готовы.");
    await refreshOperationalState();
  } catch (error) {
    showToast(friendlyError(error), true);
  } finally {
    button.disabled = false;
  }
});

refreshAll();
