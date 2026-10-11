"use strict";

// M12.19: product-facing route from reviewed needs to an immutable mixed plan,
// then onward to M12.18 manual receipt review. No retailer checkout authority.
(() => {
  const by = (id) => document.getElementById(id);
  const panel = by("mixed-shopping-panel");
  const allowed = () => !panel.hidden;
  let catalog = { items: [], skus: [] };
  let weightCandidates = [];
  let recipes = [];
  let preview = null;
  let saved = null;
  let revision = 0;
  let busy = false;
  let rows = [];

  async function api(path, payload) {
    const options = payload === undefined ? {} : {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    };
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({ error: "invalid_response" }));
    if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
    return data;
  }
  const asNumber = (value, { positive = false } = {}) => {
    const cleaned = String(value).trim().replace(/[\s\u00a0]/g, "").replace(",", ".");
    if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(cleaned) || cleaned.length > 40) {
      throw new Error("Введите корректное неотрицательное число без букв.");
    }
    if (positive && !/[1-9]/.test(cleaned)) {
      throw new Error("Для каждого продукта укажите положительную потребность.");
    }
    return cleaned;
  };
  const money = (m) => m ? `${m.amount} ${m.currency === "KGS" ? "сом" : m.currency}` : "—";
  const mass = (q) => q ? `${q.amount} ${q.unit}` : "—";
  const appendText = (parent, tag, value, className = "") => {
    const node = document.createElement(tag);
    node.textContent = String(value);
    if (className) node.className = className;
    parent.appendChild(node);
    return node;
  };
  const status = (message, error = false) => {
    by("mixed-shopping-status").textContent = message;
    by("mixed-shopping-status").classList.toggle("error", error);
  };
  function invalidate() {
    revision += 1;
    preview = null;
    by("mixed-shopping-result").hidden = true;
    by("mixed-shopping-consent").checked = false;
    by("mixed-shopping-save").disabled = true;
  }
  function setBusy(value) {
    busy = value;
    for (const id of [
      "mixed-shopping-preview", "mixed-shopping-lookup",
      "mixed-shopping-add-item", "mixed-shopping-save",
    ]) {
      by(id).disabled = value || (
        id === "mixed-shopping-save" &&
        (!preview || !by("mixed-shopping-consent").checked)
      );
    }
  }
  function selectedSource() {
    return panel.querySelector('input[name="mixed-shopping-source"]:checked')?.value || "recipe";
  }
  function availableItems() {
    const out = new Map();
    for (const sku of catalog.skus) {
      out.set(sku.item_id, {
        id: sku.item_id,
        name: catalog.items.find((x) => x.item_id === sku.item_id)?.name || sku.item_id,
        units: sku.package_quantity.unit === "l" || sku.package_quantity.unit === "ml"
          ? ["ml", "l"] : sku.package_quantity.unit === "kg" || sku.package_quantity.unit === "g"
            ? ["g", "kg"] : [sku.package_quantity.unit],
      });
    }
    for (const candidate of weightCandidates) {
      if (!out.has(candidate.item_id)) {
        out.set(candidate.item_id, {
          id: candidate.item_id, name: candidate.item_name, units: ["g", "kg"],
        });
      }
    }
    return [...out.values()].sort((a, b) => a.id.localeCompare(b.id));
  }
  function makeOption(select, id, title) {
    const option = document.createElement("option");
    option.value = id;
    option.textContent = title;
    select.appendChild(option);
  }
  function addManualRow(initial = null) {
    if (rows.length >= 32) return;
    const all = availableItems();
    if (!all.length) return;
    const wrapper = document.createElement("div");
    wrapper.className = "mixed-shopping-demand-row";
    const selector = document.createElement("select");
    selector.setAttribute("aria-label", "Нужный продукт");
    for (const entry of all) makeOption(selector, entry.id, entry.name);
    const choice = initial?.item_id || all[0].id;
    if (all.some((item) => item.id === choice)) selector.value = choice;
    const amount = document.createElement("input");
    amount.type = "text";
    amount.inputMode = "decimal";
    amount.setAttribute("aria-label", "Количество нужного продукта");
    amount.value = initial?.amount || "1";
    const unit = document.createElement("select");
    unit.setAttribute("aria-label", "Единица нужного продукта");
    const refillUnits = (requested) => {
      const chosen = all.find((item) => item.id === selector.value);
      const previous = requested || unit.value;
      unit.replaceChildren();
      for (const one of chosen.units) makeOption(unit, one, one);
      unit.value = chosen.units.includes(previous) ? previous : chosen.units[0];
    };
    refillUnits(initial?.unit);
    selector.addEventListener("change", () => refillUnits());
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-button";
    remove.textContent = "Убрать";
    remove.setAttribute("aria-label", "Убрать строку продукта");
    const row = { wrapper, selector, amount, unit };
    remove.addEventListener("click", () => {
      rows = rows.filter((entry) => entry !== row);
      wrapper.remove();
      invalidate();
    });
    wrapper.append(selector, amount, unit, remove);
    by("mixed-shopping-demands").appendChild(wrapper);
    rows.push(row);
    invalidate();
  }
  function renderRecipeOptions() {
    const select = by("mixed-shopping-recipe");
    const selected = select.value;
    select.replaceChildren();
    for (const item of recipes) makeOption(select, item.recipe_id, item.name);
    if (recipes.some((r) => r.recipe_id === selected)) select.value = selected;
  }
  function buildInput() {
    const budget = {
      amount: asNumber(by("mixed-shopping-budget").value, { positive: true }),
      currency: "KGS",
    };
    if (selectedSource() === "recipe") {
      const id = by("mixed-shopping-recipe").value;
      if (!id) throw new Error("Выберите блюдо.");
      return { recipe_id: id, budget };
    }
    if (!rows.length || rows.length > 32) {
      throw new Error("Добавьте от 1 до 32 продуктов.");
    }
    return {
      demands: rows.map((row) => ({
        item_id: row.selector.value,
        quantity: {
          amount: asNumber(row.amount.value, { positive: true }),
          unit: row.unit.value,
        },
      })),
      budget,
    };
  }
  function addSource(target, source) {
    // Seller URL is evidence supplied by the trusted backend, never a signal
    // that checkout is enabled. No javascript/data links accepted.
    try {
      const parsed = new URL(source);
      if (parsed.protocol !== "https:" || parsed.hostname !== "globus-online.kg") return;
      const anchor = document.createElement("a");
      anchor.href = parsed.href;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = "Открыть публичную карточку (не правило заказа)";
      target.appendChild(anchor);
    } catch (_) {
      // Source identity still remains in the persisted server-side snapshot.
    }
  }
  function renderPreview(data) {
    const record = data.plan;
    const target = by("mixed-shopping-lines");
    target.replaceChildren();
    appendText(target, "p",
      `Общий бюджет: ${money(record.budget)}. Оценка товаров: ${money(record.estimated_total)}. Доставка и итоговый чек пока неизвестны.`);
    appendText(target, "p",
      `Предпросмотр действителен до ${new Date(data.preview_expires_at).toLocaleString("ru-RU")}. Сохранение плана не означает покупку.`);
    for (const p of record.packages) {
      const card = appendText(target, "article", "", "mixed-shopping-line");
      appendText(card, "strong", `${p.item_id} — фасованный товар`);
      appendText(card, "p", `${p.packs} уп. × ${mass(p.package)}; ${money(p.cost)}. Продавец: ${p.seller_id}.`);
    }
    for (const w of record.weights) {
      const card = appendText(target, "article", "", "mixed-shopping-line");
      appendText(card, "strong", `${w.item.name} — весовой товар`);
      appendText(card, "p", `Нужно ${mass(w.requested)}, к продаже ${mass(w.sellable)}; ориентир ${money(w.estimated_upper_bound)}.`);
      appendText(card, "p",
        `Идентификатор товара: ${w.rule.source_ref}. Допущенные доверенным пилотом параметры: минимум ${mass(w.rule.minimum)}, шаг ${mass(w.rule.increment)}, отметка проверки ${w.rule.verified_at}. Публичная карточка сама не доказывает эти параметры.`);
      addSource(card, w.observation.source_ref);
    }
    appendText(target, "p",
      "Внимание: публичная карточка Globus даёт справочную цену за кг, но не гарантирует доступность или заказ этого количества.");
    by("mixed-shopping-result").hidden = false;
    by("mixed-shopping-saved").hidden = true;
    by("mixed-shopping-consent").checked = false;
    by("mixed-shopping-save").disabled = true;
  }
  function renderSaved(record) {
    saved = record;
    preview = null;
    by("mixed-shopping-result").hidden = true;
    by("mixed-shopping-saved").hidden = false;
    by("mixed-shopping-saved-info").textContent =
      `Сохранён план ${record.plan_id}. Оценка стоимости товаров: ${money(record.estimated_total)}. ` +
      "Покупка в магазине не оформлена, домашние остатки не изменены.";
    by("mixed-shopping-lookup-id").value = record.plan_id;
    status("План сохранён. После фактической покупки откройте сверку чека.");
  }
  async function doPreview(event) {
    event.preventDefault();
    if (!allowed() || busy) return;
    invalidate();
    by("mixed-shopping-saved").hidden = true;
    saved = null;
    let payload;
    try {
      payload = buildInput();
    } catch (error) {
      status(error.message, true);
      return;
    }
    const requestRevision = revision;
    setBusy(true);
    status("Получаем цены и проверяем правила заказа, остатки и весь бюджет…");
    try {
      const data = await api("/mixed/plans/preview", payload);
      if (requestRevision !== revision || !allowed()) return;
      if (data.persisted !== false || data.checkout_confirmed !== false) {
        throw new Error("Предпросмотр не подтвердил безопасную границу покупки.");
      }
      preview = data;
      renderPreview(data);
      status("Все недостающие продукты включены в предварительный план. Проверьте источники и бюджет.");
    } catch (error) {
      if (requestRevision === revision) {
        status(`Не удалось рассчитать весь план: ${error.message}. Неполный список не сохраняется.`, true);
      }
    } finally {
      setBusy(false);
    }
  }
  async function savePlan() {
    if (!allowed() || busy || !preview || !by("mixed-shopping-consent").checked) return;
    const id = preview.plan.plan_id;
    setBusy(true);
    status("Сохраняем список покупок без записи факта покупки…");
    try {
      const data = await api("/mixed/plans/confirm", { plan_id: id });
      if (data.persisted !== true || data.checkout_confirmed !== false ||
          data.plan.plan_id !== id) {
        throw new Error("Ответ сохранения не соответствует предпросмотру.");
      }
      renderSaved(data.plan);
    } catch (error) {
      // A lost HTTP response might happen after the server's durable commit.
      // Check the exact immutable ID, not an invented second checkout.
      try {
        const result = await api(`/mixed/plans/${encodeURIComponent(id)}`);
        if (result.plan?.plan_id === id) {
          renderSaved(result.plan);
          return;
        }
      } catch (_) { /* status still unknown; require a new review */ }
      invalidate();
      status(`Сохранение не подтверждено: ${error.message}. Проверьте ID или создайте новый предпросмотр.`, true);
    } finally {
      setBusy(false);
    }
  }
  async function lookupPlan(event) {
    event.preventDefault();
    if (!allowed() || busy) return;
    const id = by("mixed-shopping-lookup-id").value.trim();
    if (!/^mp-[a-z0-9_-]{1,64}$/.test(id)) {
      status("Введите полный ID смешанного плана в формате mp-…", true);
      return;
    }
    setBusy(true);
    try {
      const result = await api(`/mixed/plans/${encodeURIComponent(id)}`);
      if (result.plan?.plan_id !== id) throw new Error("ID ответа не совпадает с запросом.");
      renderSaved(result.plan);
    } catch (error) {
      status(`Не удалось открыть сохранённый план: ${error.message}`, true);
    } finally {
      setBusy(false);
    }
  }
  function openReceipt() {
    if (!saved || typeof window.MixedReceiptReview?.openPlan !== "function") {
      status("Интерфейс сверки чека пока недоступен.", true);
      return;
    }
    if (!window.MixedReceiptReview.openPlan(saved.plan_id)) {
      status("Сверка чека не открылась: сначала проверьте доступ приватного пилота.", true);
    }
  }

  panel.addEventListener("input", (event) => {
    if (event.target.id === "mixed-shopping-consent") return;
    if (by("mixed-shopping-lookup-form").contains(event.target)) {
      saved = null;
      by("mixed-shopping-saved").hidden = true;
      return;
    }
    invalidate();
  });
  panel.addEventListener("change", (event) => {
    if (event.target.id === "mixed-shopping-consent") {
      by("mixed-shopping-save").disabled = busy ||
        !preview || !event.target.checked;
      return;
    }
    if (by("mixed-shopping-lookup-form").contains(event.target)) return;
    if (event.target.name === "mixed-shopping-source") {
      const manual = selectedSource() === "manual";
      by("mixed-shopping-recipe-input").hidden = manual;
      by("mixed-shopping-manual-input").hidden = !manual;
    }
    invalidate();
  });
  by("mixed-shopping-form").addEventListener("submit", doPreview);
  by("mixed-shopping-save").addEventListener("click", savePlan);
  by("mixed-shopping-lookup-form").addEventListener("submit", lookupPlan);
  by("mixed-shopping-open-receipt").addEventListener("click", openReceipt);
  by("mixed-shopping-add-item").addEventListener("click", () => addManualRow());
  by("shopping-to-mixed-recipe").addEventListener("click", () => {
    if (!allowed()) return;
    panel.open = true;
    panel.querySelector('input[value="recipe"][name="mixed-shopping-source"]').checked = true;
    by("mixed-shopping-recipe-input").hidden = false;
    by("mixed-shopping-manual-input").hidden = true;
    by("mixed-shopping-recipe").value = by("shopping-recipe-select").value;
    invalidate();
    panel.scrollIntoView({ block: "start", behavior: "smooth" });
  });

  window.MixedShoppingWorkflow = {
    configure(data) {
      catalog = data.catalog || { items: [], skus: [] };
      weightCandidates = data.candidates || [];
      if (!rows.length) {
        addManualRow(); // The operator must choose the needed Item and quantity.
      }
    },
    setRecipes(items) {
      recipes = items || [];
      renderRecipeOptions();
    },
    setEnabled(value) {
      panel.hidden = !value;
      by("shopping-to-mixed-recipe").hidden = !value;
      if (!value) {
        invalidate();
        saved = null;
        by("mixed-shopping-saved").hidden = true;
      }
    },
  };
})();
