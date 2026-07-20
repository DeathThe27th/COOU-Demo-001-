/* Live-refresh the session attendance table while the session is active. */
(function () {
  const table = document.getElementById("attendance-table");
  if (!table || table.dataset.active !== "1") return;

  const pillClass = {
    present: "pill pill-green",
    flagged_manual_review: "pill pill-amber",
    absent: "pill pill-red",
  };

  async function refresh() {
    try {
      const res = await fetch(table.dataset.liveUrl);
      if (!res.ok) return;
      const rows = await res.json();
      const tbody = table.querySelector("tbody");
      tbody.innerHTML = rows.map((r) => `
        <tr>
          <td>${r.matric}</td>
          <td>${r.name}</td>
          <td><span class="${pillClass[r.status] || "pill"}">${r.status}</span></td>
          <td>${r.time || "—"}</td>
        </tr>`).join("");
    } catch (_) { /* transient network error — next poll will recover */ }
  }

  setInterval(refresh, 4000);
})();
