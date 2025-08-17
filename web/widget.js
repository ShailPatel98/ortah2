(function () {
  const API_CHAT = "/chat";
  const widget = document.getElementById("widget");
  const fab = document.getElementById("fab");
  const chatEl = document.getElementById("chat");
  const input = document.getElementById("msg");
  const sendBtn = document.getElementById("send");

  // Stable session per browser
  const sessionId = localStorage.getItem("ortahaus_session") || (() => {
    const id = "sess_" + Math.random().toString(36).slice(2);
    localStorage.setItem("ortahaus_session", id);
    return id;
  })();

  // Helpers
  function scrollToBottom() {
    chatEl.scrollTop = chatEl.scrollHeight;
  }
  function escapeHtml(s) {
    return s.replace(/[&<>]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[ch]));
  }
  function addUser(text) {
    const row = document.createElement("div");
    row.className = "m you";
    row.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
    chatEl.appendChild(row); scrollToBottom();
  }
  function addBot(html) {
    const row = document.createElement("div");
    row.className = "m";
    row.innerHTML = `<div class="bubble">${html}</div>`;
    chatEl.appendChild(row); scrollToBottom();
  }
  async function send() {
    const text = input.value.trim();
    if (!text) return;
    addUser(text);
    input.value = "";
    try {
      const res = await fetch(API_CHAT, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ message: text, session_id: sessionId })
      });
      const data = await res.json();
      addBot(data.reply || "Hmm, I didn’t catch that.");
    } catch (e) {
      addBot("Sorry—server error. Please try again.");
    }
    input.focus();
  }

  // Open/close widget
  function openWidget() {
    widget.classList.add("open");
    if (!chatEl.dataset.greeted) {
      chatEl.dataset.greeted = "1";
      addBot("Hi! I’m the Ortahaus Product Guide. Tell me your hair type and your main concern (volume, hold, frizz, shine, or hydration). I’ll ask a quick follow-up if needed.");
    }
    // On mobile, ensure viewport visible over keyboard
    setTimeout(scrollToBottom, 50);
    input.focus({ preventScroll: true });
  }
  function closeWidget() {
    widget.classList.remove("open");
  }

  fab.addEventListener("click", () => {
    widget.classList.contains("open") ? closeWidget() : openWidget();
  });
  sendBtn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });

  // Improve mobile keyboard behavior
  window.addEventListener("resize", () => {
    // When keyboard opens, keep latest message in view
    scrollToBottom();
  });

  // Auto-open on very small screens to make it obvious
  if (window.matchMedia("(max-width: 480px)").matches) {
    window.addEventListener("load", () => setTimeout(openWidget, 300));
  }
})();
