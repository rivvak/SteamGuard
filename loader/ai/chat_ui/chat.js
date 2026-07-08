(() => {
  const cfg = window.__SG_AI_CONFIG__;
  const log = document.getElementById("log");
  const composer = document.getElementById("composer");
  const q = document.getElementById("q");
  const send = document.getElementById("send");

  function append(role, text) {
    const div = document.createElement("div");
    div.className = `msg ${role}`;
    div.textContent = text;
    log.appendChild(div);
    log.scrollTop = log.scrollHeight;
    return div;
  }

  async function ask(question) {
    const bubble = append("assistant", "");
    send.disabled = true;
    q.disabled = true;

    try {
      const resp = await fetch(`${cfg.serverUrl}/ai/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
        body: JSON.stringify({
          key: cfg.key,
          hwid: cfg.hwid,
          sig: cfg.sig,
          question,
        }),
      });

      if (!resp.ok || !resp.body) {
        bubble.textContent = `Sorry — the assistant is unavailable (${resp.status}).`;
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buf = "";
      let answer = "";
      let sources = [];

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        const events = buf.split("\n\n");
        buf = events.pop() || "";

        for (const evt of events) {
          const line = evt.trim();
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (payload === "[DONE]") continue;
          try {
            const obj = JSON.parse(payload);
            if (obj.delta) {
              answer += obj.delta;
              bubble.textContent = answer;
              log.scrollTop = log.scrollHeight;
            }
            if (obj.sources) sources = obj.sources;
          } catch (e) {
            // ignore malformed frame
          }
        }
      }

      if (sources.length) {
        const s = document.createElement("span");
        s.className = "sources";
        s.textContent = `Sources: ${sources.slice(0, 3).join(", ")}`;
        bubble.appendChild(s);
      }
    } catch (e) {
      bubble.textContent = `Network error: ${e && e.message ? e.message : e}`;
    } finally {
      send.disabled = false;
      q.disabled = false;
      q.focus();
    }
  }

  composer.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = q.value.trim();
    if (!text) return;
    append("user", text);
    q.value = "";
    ask(text);
  });

  q.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      composer.requestSubmit();
    }
  });

  q.focus();
})();
