"use strict";

(() => {
  const button = document.getElementById("reset-local-data");
  if (!button) return;

  button.addEventListener("click", async () => {
    const confirmed = window.confirm(
      [
        "Сбросить все локальные данные?",
        "",
        "Будут удалены:",
        "• текущие домашние запасы",
        "• история изменений и покупок",
        "• обученные оценки",
        "• сохранённые планы",
        "",
        "Это действие нельзя отменить.",
      ].join("\n"),
    );
    if (!confirmed) return;

    button.disabled = true;
    try {
      const response = await fetch("/local-data/reset", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ confirmation: "RESET" }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          payload.detail || "Не удалось сбросить локальные данные.",
        );
      }

      sessionStorage.removeItem("hsp:first-use-dismissed");
      window.location.reload();
    } catch (error) {
      window.alert(
        error?.message || "Не удалось сбросить локальные данные.",
      );
      button.disabled = false;
    }
  });
})();
