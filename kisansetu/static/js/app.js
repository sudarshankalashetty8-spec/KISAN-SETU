document.addEventListener("DOMContentLoaded", () => {
    const flash = document.querySelector(".flash-wrap");
    if (flash) { setTimeout(() => flash.style.opacity = "0", 4000); setTimeout(() => flash.remove(), 4600); }
    const dateInput = document.querySelector('input[name="slot_date"]');
    if (dateInput && !dateInput.value) { const d = new Date(); const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000); dateInput.min = local.toISOString().slice(0,10); }

    const selector = document.getElementById("languageSelect");
    if (selector) selector.addEventListener("change", e => {
        const next = encodeURIComponent(window.location.pathname + window.location.search);
        window.location.href = `/set-language/${encodeURIComponent(e.target.value)}?next=${next}`;
    });


    // Translate exact English phrases in static templates using the selected language dictionary.
    // Existing Jinja t() translations remain the primary translation mechanism.
    const englishDict = window.KISANSETU_EN || {};
    const activeDict = window.KISANSETU_I18N || {};
    const phraseMap = new Map();
    Object.keys(englishDict).forEach(key => { if (activeDict[key] && englishDict[key]) phraseMap.set(String(englishDict[key]).trim(), String(activeDict[key])); });
    if (phraseMap.size) {
        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
        const nodes = []; while (walker.nextNode()) nodes.push(walker.currentNode);
        nodes.forEach(node => { const raw = node.nodeValue; const trimmed = raw.trim(); if (phraseMap.has(trimmed)) node.nodeValue = raw.replace(trimmed, phraseMap.get(trimmed)); });
        document.querySelectorAll('input[placeholder], textarea[placeholder], button[title], [aria-label]').forEach(el => {
            ['placeholder','title','aria-label'].forEach(attr => { const val=el.getAttribute(attr); if (val && phraseMap.has(val.trim())) el.setAttribute(attr, phraseMap.get(val.trim())); });
        });
    }

    const dict = window.KISANSETU_I18N || {};
    const tr = (key, fallback) => dict[key] || fallback || key;
    const queueTable = document.querySelector("#queue-table tbody");
    if (queueTable) {
        async function refreshQueue() {
            try {
                const response = await fetch("/api/queue"); const rows = await response.json();
                document.querySelector("#queue-count").textContent = rows.length;
                queueTable.innerHTML = rows.length ? rows.map(r => `<tr><td><strong class="token">${r.token}</strong></td><td>${r.name}</td><td>${r.crop}</td><td>${r.slot_time}</td><td><span class="badge">${r.status}</span></td></tr>`).join("") : `<tr><td colspan="5" class="empty">${tr("no_current_bookings")}</td></tr>`;
            } catch (e) { console.log("Queue refresh failed", e); }
        }
        setInterval(refreshQueue, 15000);
    }
});
