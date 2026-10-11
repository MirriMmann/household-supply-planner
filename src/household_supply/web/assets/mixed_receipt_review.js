"use strict";

// M12.18: private operator-attested receipt review. No store checkout or
// automatic purchase. The server alone owns validation and event authority.
(() => {
  const by = (id) => document.getElementById(id);
  const panel = by("mixed-receipt-review-panel");
  const api = async (path, payload) => {
    const response = await fetch(path, payload === undefined ? {} : {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({ error: "invalid_response" }));
    if (!response.ok) throw new Error(body.detail || body.error || `HTTP ${response.status}`);
    return body;
  };
  let plan = null;
  let planId = null;
  let revision = 0;
  let reviewed = null;
  let busy = false;
  let fields = [];

  function money(m) {
    return `${m?.amount ?? "—"} ${m?.currency === "KGS" ? "сом" : m?.currency ?? ""}`;
  }
  function quantity(q) {
    return q ? `${q.amount} ${q.unit}` : "—";
  }
  function labelFor(id) {
    return (typeof itemName === "function" ? itemName(id) : id) || id;
  }
  function text(parent, tag, content, className = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = content;
    parent.appendChild(node);
    return node;
  }
  function input(parent, id, label, value, attrs = {}) {
    const wrap = document.createElement("label");
    wrap.className = "mixed-receipt-field";
    text(wrap, "span", label);
    const node = document.createElement("input");
    node.id = id;
    node.type = attrs.type || "text";
    node.inputMode = attrs.inputMode || "decimal";
    node.value = String(value);
    node.required = true;
    if (attrs.min !== undefined) node.min = String(attrs.min);
    if (attrs.max !== undefined) node.max = String(attrs.max);
    if (attrs.step !== undefined) node.step = String(attrs.step);
    wrap.appendChild(node);
    parent.appendChild(wrap);
    return node;
  }
  function setStatus(message, error = false) {
    const node = by("mixed-receipt-status");
    node.textContent = message;
    node.classList.toggle("error", error);
  }
  function setBusy(next) {
    busy = next;
    by("mixed-receipt-load").disabled = next;
    by("mixed-receipt-preview").disabled = next;
    by("mixed-receipt-confirm").disabled = next ||
      !reviewed || !by("mixed-receipt-final-consent").checked;
    by("mixed-receipt-retry").disabled = next ||
      !by("mixed-receipt-retry-consent").checked;
  }
  function invalidate() {
    ++revision;
    reviewed = null;
    by("mixed-receipt-reviewed").hidden = true;
    by("mixed-receipt-final-consent").checked = false;
    by("mixed-receipt-confirm").disabled = true;
  }

  // For reviewed input strings the backend, not browser arithmetic, verifies
  // total_paid=sum(actual line prices)+fees-discount and budget limit.
  function numberValue(raw, { positive = false } = {}) {
    const value = String(raw).trim().replace(/[\s\u00a0]/g, "").replace(",", ".");
    if (!/^(?:0|[1-9]\d*)(?:\.\d+)?$/.test(value) || value.length > 40) {
      throw new Error("Используйте неотрицательное число без округления и без букв.");
    }
    if (positive && !/[1-9]/.test(value)) {
      throw new Error("Количество фактически полученного товара должно быть больше нуля.");
    }
    return value;
  }
  function moneyObject(raw) {
    return { amount: numberValue(raw), currency: plan.budget.currency };
  }
  function buildPayload() {
    if (!plan || !planId) throw new Error("Сначала загрузите сохранённый план.");
    const timestamp = new Date(by("mixed-receipt-time").value);
    if (!Number.isFinite(timestamp.getTime())) throw new Error("Укажите дату получения.");
    const receiptRef = by("mixed-receipt-ref").value.trim();
    if (!receiptRef) throw new Error("Укажите номер или ссылку на чек.");
    const lines = fields.map((row) => {
      const paid = moneyObject(row.paid.value);
      if (row.kind === "fixed_package") {
        const packs = Number(row.count.value);
        if (!Number.isSafeInteger(packs) || packs < 1 || packs > row.plannedPacks) {
          throw new Error(`${row.name}: количество упаковок вне плана.`);
        }
        return { kind: "fixed_package", item_id: row.itemId, sku_id: row.skuId, packs, paid };
      }
      return {
        kind: "weighted", item_id: row.itemId,
        actual_mass: {
          amount: numberValue(row.mass.value, { positive: true }),
          unit: row.unit.value,
        },
        paid,
      };
    });
    return {
      receipt_ref: receiptRef,
      occurred_at: timestamp.toISOString(),
      lines,
      delivery_and_fees: moneyObject(by("mixed-receipt-fees").value),
      discount: moneyObject(by("mixed-receipt-discount").value),
      total_paid: moneyObject(by("mixed-receipt-total").value),
      price_changes_approved: by("mixed-receipt-price-approval").checked,
    };
  }
  function renderPlan(record) {
    fields = [];
    by("mixed-receipt-lines").replaceChildren();
    text(by("mixed-receipt-plan-summary"), "span",
      `План ${record.plan_id}. Бюджет: ${money(record.budget)}. ` +
      `Оценка товаров: ${money(record.estimated_total)}. ` +
      "Сверьте полученное по настоящему чеку: сумма и масса могут отличаться.");
    const candidates = [
      ...record.packages.map((p) => ({ kind: "fixed_package", data: p, itemId: p.item_id })),
      ...record.weights.map((w) => ({ kind: "weighted", data: w, itemId: w.item.id })),
    ].sort((a, b) => a.itemId.localeCompare(b.itemId));

    for (const [i, row] of candidates.entries()) {
      const card = document.createElement("section");
      card.className = "mixed-receipt-line";
      const name = row.kind === "weighted" ? row.data.item.name : labelFor(row.itemId);
      text(card, "h4", name);
      const saved = row.data;
      if (row.kind === "fixed_package") {
        text(card, "p",
          `По плану: ${saved.packs} уп. × ${quantity(saved.package)}, ` +
          `стоимость ${money(saved.cost)}. SKU: ${saved.sku_id}.`);
        const count = input(card, `mixed-count-${i}`, "Фактически получено упаковок",
          saved.packs, { type: "number", inputMode: "numeric", min: 1, max: saved.packs, step: 1 });
        const paid = input(card, `mixed-paid-${i}`, "Фактически оплачено (сом)", saved.cost.amount);
        fields.push({
          kind: row.kind, itemId: row.itemId, name,
          count, paid, skuId: saved.sku_id, plannedPacks: saved.packs,
        });
      } else {
        text(card, "p",
          `По плану: ${quantity(saved.sellable)}, ` +
          `ориентир стоимости ${money(saved.estimated_upper_bound)}. ` +
          "Вес в чеке может быть меньше.");
        const mass = input(card, `mixed-mass-${i}`, "Фактически полученная масса", saved.sellable.amount);
        const unit = document.createElement("select");
        unit.id = `mixed-unit-${i}`;
        unit.setAttribute("aria-label", `${name}: единица массы`);
        for (const u of ["g", "kg"]) {
          const option = document.createElement("option");
          option.value = u;
          option.textContent = u === "g" ? "граммы" : "килограммы";
          unit.appendChild(option);
        }
        unit.value = saved.sellable.unit;
        card.appendChild(unit);
        const paid = input(card, `mixed-paid-${i}`, "Фактически оплачено (сом)",
          saved.estimated_upper_bound.amount);
        fields.push({ kind: row.kind, itemId: row.itemId, name, mass, unit, paid });
      }
      by("mixed-receipt-lines").appendChild(card);
    }
    by("mixed-receipt-total").value = record.estimated_total.amount;
    by("mixed-receipt-ref").value = "";
    by("mixed-receipt-fees").value = "0";
    by("mixed-receipt-discount").value = "0";
    by("mixed-receipt-price-approval").checked = false;
    const now = new Date(Math.max(Date.now(), Date.parse(record.created_at) + 1000));
    const local = new Date(now.getTime() - now.getTimezoneOffset() * 60000);
    by("mixed-receipt-time").value = local.toISOString().slice(0, 19);
    by("mixed-receipt-claimed").hidden = true;
    by("mixed-receipt-editor").hidden = false;
    invalidate();
  }
  function renderClaimed(status) {
    invalidate();
    by("mixed-receipt-editor").hidden = true;
    by("mixed-receipt-claimed").hidden = false;
    const details = by("mixed-receipt-claimed-details");
    details.replaceChildren();
    const confirmed = status.state === "completed";
    text(details, "p", confirmed
      ? "Чек уже обработан: все события этой покупки записаны."
      : "Чек уже закреплён, но публикация событий ещё не завершена.");
    text(details, "p", `Чек: ${status.receipt?.receipt_ref || "—"}. ` +
      `Оплачено: ${money(status.receipt?.total_paid)}.`);
    if (status.fulfillment) {
      for (const line of status.fulfillment) {
        text(details, "p", `${labelFor(line.item_id)}: получено ` +
          `${quantity(line.actual_received)}, ещё требуется ` +
          `${quantity(line.still_missing_after_receipt)}.`);
      }
    }
    by("mixed-receipt-retry-consent").checked = false;
    by("mixed-receipt-retry-label").hidden = confirmed;
    by("mixed-receipt-retry").hidden = confirmed;
    by("mixed-receipt-retry").disabled = true;
    setStatus(confirmed
      ? "Покупка уже учтена. Повторная запись не требуется."
      : "Ожидает восстановления. Можно явно повторить запись того же сохранённого чека.");
  }
  async function load(event) {
    event.preventDefault();
    if (busy) return;
    const id = by("mixed-receipt-plan-id").value.trim();
    if (!/^mp-[a-z0-9_-]{1,64}$/.test(id)) {
      setStatus("Введите ID сохранённого смешанного плана формата mp-…", true);
      return;
    }
    const loadRevision = ++revision;
    plan = null;
    planId = null;
    reviewed = null;
    fields = [];
    by("mixed-receipt-editor").hidden = true;
    by("mixed-receipt-claimed").hidden = true;
    setBusy(true);
    try {
      const [saved, status] = await Promise.all([
        api(`/mixed/plans/${encodeURIComponent(id)}`),
        api(`/mixed/plans/${encodeURIComponent(id)}/receipt`),
      ]);
      if (saved.plan.plan_id !== id || status.plan_id !== id) {
        throw new Error("Идентификатор плана в ответе не совпадает.");
      }
      if (loadRevision !== revision || by("mixed-receipt-plan-id").value.trim() !== id) {
        return; // Plan ID changed while fetching; discard the stale reply.
      }
      plan = saved.plan;
      planId = id;
      if (status.state === "not_received") {
        renderPlan(plan);
        setStatus("План загружен. Проверьте значения по фактическому чеку.");
      } else {
        renderClaimed(status);
      }
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      setBusy(false);
    }
  }
  function reviewedResult(result) {
    const target = by("mixed-receipt-reviewed-details");
    target.replaceChildren();
    text(target, "p", `Сумма по чеку: ${money(result.total_paid)}. ` +
      `Бюджет: ${money(result.budget)}. ` +
      `Остаток бюджета: ${money(result.budget_remaining_after_paid)}.`);
    text(target, "p", result.all_demands_fulfilled
      ? "Все запланированные дефициты покрыты фактическими количествами."
      : "Внимание: после получения останутся непокрытые потребности.");
    for (const line of result.lines) {
      text(target, "p", `${labelFor(line.item_id)}: получено ` +
        `${quantity(line.actual_received)}; ещё не хватает ` +
        `${quantity(line.still_missing_after_receipt)}; оплачено ${money(line.paid)}.`);
    }
    text(target, "p",
      "Проверка ничего не записала. Данные будут внесены только после отдельного подтверждения.");
    by("mixed-receipt-reviewed").hidden = false;
    by("mixed-receipt-final-consent").checked = false;
    by("mixed-receipt-confirm").disabled = true;
  }
  async function preview(event) {
    event.preventDefault();
    if (busy || !planId) return;
    invalidate();
    let payload;
    try {
      payload = buildPayload();
    } catch (error) {
      setStatus(error.message, true);
      return;
    }
    const atRevision = revision;
    setBusy(true);
    setStatus("Проверяем чек без записи домашних запасов…");
    try {
      const result = await api(
        `/mixed/plans/${encodeURIComponent(planId)}/receipt/preview`, payload
      );
      if (atRevision !== revision || !plan || result.plan_id !== planId) return;
      reviewed = JSON.stringify(payload);
      reviewedResult(result);
      setStatus("Чек проверен. Запасы пока не изменились.");
    } catch (error) {
      if (atRevision === revision) setStatus(error.message, true);
    } finally {
      setBusy(false);
    }
  }
  async function confirm() {
    if (busy || !reviewed || !by("mixed-receipt-final-consent").checked) return;
    const payload = JSON.parse(reviewed);
    const id = planId;
    setBusy(true);
    setStatus("Подтверждаем получение и публикуем все товары одной транзакцией…");
    try {
      const result = await api(`/mixed/plans/${encodeURIComponent(id)}/receipt`, payload);
      renderClaimed(result);
      if (typeof refreshOperationalState === "function") await refreshOperationalState();
    } catch (error) {
      setStatus(`Не удалось подтвердить: ${error.message}. Перезагрузите статус чека перед повтором.`, true);
      // A network failure can happen *after* the server commits. Never
      // leave an editable form that could be mistaken for an unclaimed plan.
      by("mixed-receipt-editor").hidden = true;
      reviewed = null;
    } finally {
      setBusy(false);
    }
  }
  async function recover() {
    if (busy || !planId || !by("mixed-receipt-retry-consent").checked) return;
    setBusy(true);
    setStatus("Повторяем сохранённый чек без изменения его данных…");
    try {
      const result = await api(
        `/mixed/plans/${encodeURIComponent(planId)}/receipt/retry`,
        { confirmation: "RETRY" },
      );
      renderClaimed(result);
      if (typeof refreshOperationalState === "function") await refreshOperationalState();
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      setBusy(false);
    }
  }

  panel.addEventListener("input", (event) => {
    if (event.target.id === "mixed-receipt-plan-id") {
      invalidate();
      by("mixed-receipt-editor").hidden = true;
      by("mixed-receipt-claimed").hidden = true;
      plan = null;
      planId = null;
    } else if (
      by("mixed-receipt-editor").contains(event.target)
      && event.target.id !== "mixed-receipt-final-consent"
    ) {
      invalidate();
    }
  });
  panel.addEventListener("change", (event) => {
    if (by("mixed-receipt-editor").contains(event.target)
      && event.target.id !== "mixed-receipt-final-consent") invalidate();
  });
  by("mixed-receipt-lookup-form").addEventListener("submit", load);
  by("mixed-receipt-form").addEventListener("submit", preview);
  by("mixed-receipt-confirm").addEventListener("click", confirm);
  by("mixed-receipt-retry").addEventListener("click", recover);
  by("mixed-receipt-final-consent").addEventListener("change", () => {
    by("mixed-receipt-confirm").disabled = busy ||
      !reviewed || !by("mixed-receipt-final-consent").checked;
  });
  by("mixed-receipt-retry-consent").addEventListener("change", () => {
    by("mixed-receipt-retry").disabled = busy ||
      !by("mixed-receipt-retry-consent").checked;
  });
  window.MixedReceiptReview = {
    setEnabled(allowed) {
      panel.hidden = !Boolean(allowed);
      if (!allowed) {
        by("mixed-receipt-editor").hidden = true;
        by("mixed-receipt-claimed").hidden = true;
        plan = null;
        planId = null;
        invalidate();
      }
    },
  };
})();
