/*
 * Chat widget: posts to /api/chat and renders the transcript.
 *
 * Assistant replies arrive as light markdown (`**bold**`, bullet lists, `code`),
 * which used to show up as literal asterisks. The renderer escapes the text
 * first and only then turns formatting into elements, so nothing the model (or a
 * product description) returns can ever be injected as raw HTML.
 */
(function () {
  const chatWindow = document.getElementById("chat-window");
  const messageInput = document.getElementById("message-input");
  const emailInput = document.getElementById("email-input");
  const sendBtn = document.getElementById("send-btn");
  const chatForm = document.getElementById("chat-form");
  const newChatBtn = document.getElementById("new-chat-btn");
  const suggestions = document.getElementById("chat-suggestions");

  const GREETING =
    "Hi! I'm the NovaTech AI assistant. Ask me about products, orders, delivery, or anything else about the store.";
  const STORAGE_KEY = "novatech.chat.v1";

  let conversationId = null;
  let busy = false;

  // ---------------------------------------------------------------- helpers
  function clockTime() {
    return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function scrollToBottom() {
    chatWindow.scrollTop = chatWindow.scrollHeight;
  }

  function autosize() {
    messageInput.style.height = "auto";
    messageInput.style.height = Math.min(messageInput.scrollHeight, 160) + "px";
  }

  function setBusy(value) {
    busy = value;
    sendBtn.disabled = value;
    chatForm.setAttribute("aria-busy", String(value));
    if (!value) messageInput.focus();
  }

  // Best effort: the widget still works when storage is unavailable (private mode).
  function readStoredState() {
    try {
      return JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "{}");
    } catch (err) {
      return {};
    }
  }

  function storeState() {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ conversationId: conversationId, email: emailInput.value.trim() })
      );
    } catch (err) {
      /* ignore */
    }
  }

  // ------------------------------------------------------------- markdown
  const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (ch) => ESCAPES[ch]);
  }

  function inlineMarkdown(escaped) {
    return escaped
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|\s)\*([^*\n]+)\*/g, "$1<em>$2</em>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  }

  function renderMarkdown(target, text) {
    let list = null;

    String(text)
      .split(/\r?\n/)
      .forEach((rawLine) => {
        const line = rawLine.trim();
        if (!line) {
          list = null;
          return;
        }

        const bullet = line.match(/^[-*\u2022]\s+(.*)$/);
        const numbered = line.match(/^\d+[.)]\s+(.*)$/);

        if (bullet || numbered) {
          const tag = bullet ? "ul" : "ol";
          if (!list || list.tagName !== tag.toUpperCase()) {
            list = document.createElement(tag);
            target.appendChild(list);
          }
          const item = document.createElement("li");
          item.innerHTML = inlineMarkdown(escapeHtml((bullet || numbered)[1]));
          list.appendChild(item);
          return;
        }

        list = null;
        const paragraph = document.createElement("p");
        paragraph.innerHTML = inlineMarkdown(escapeHtml(line));
        target.appendChild(paragraph);
      });
  }
// ------------------------------------------------------------ rendering
  function addMessage(role, text, options) {
    const opts = options || {};
    const row = document.createElement("div");
    row.className = "msg msg-" + role + (opts.error ? " msg-error" : "");

    const avatar = document.createElement("span");
    avatar.className = "avatar " + (role === "user" ? "avatar-user" : "avatar-bot");
    avatar.setAttribute("aria-hidden", "true");
    avatar.textContent = role === "user" ? "You" : "NT";

    const bubble = document.createElement("div");
    bubble.className =
      "bubble " + (role === "user" ? "bubble-user" : "bubble-assistant") + (opts.error ? " bubble-error" : "");

    if (role !== "user" && (opts.intent || opts.time)) {
      const meta = document.createElement("div");
      meta.className = "bubble-meta";
      if (opts.intent) {
        const badge = document.createElement("span");
        badge.className = "badge badge-" + opts.intent;
        badge.textContent = opts.intent + " agent";
        meta.appendChild(badge);
      }
      if (opts.time) {
        const time = document.createElement("span");
        time.textContent = "at " + opts.time;
        meta.appendChild(time);
      }
      bubble.appendChild(meta);
    }

    const content = document.createElement("div");
    content.className = "bubble-content";
    if (role === "user" || opts.error) {
      content.textContent = text; // user input and diagnostics stay literal
    } else {
      renderMarkdown(content, text);
    }
    bubble.appendChild(content);

    row.appendChild(avatar);
    row.appendChild(bubble);
    chatWindow.appendChild(row);
    if (opts.escalated && role !== "user") {
      addNote("This reply promised a human follow-up and is waiting in the admin Support queue.", "msg-note-warn");
    }
    scrollToBottom();
  }

  function addNote(text, extraClass) {
    const note = document.createElement("div");
    note.className = "msg-note" + (extraClass ? " " + extraClass : "");
    note.textContent = text;
    chatWindow.appendChild(note);
    scrollToBottom();
  }

  function addTyping() {
    const row = document.createElement("div");
    row.className = "msg msg-assistant";
    row.id = "typing-indicator";
    row.innerHTML =
      '<span class="avatar avatar-bot" aria-hidden="true">NT</span>' +
      '<div class="bubble bubble-assistant typing">' +
      '<span class="dot" aria-hidden="true"></span>' +
      '<span class="dot" aria-hidden="true"></span>' +
      '<span class="dot" aria-hidden="true"></span>' +
      '<span class="sr-only">NovaTech assistant is typing</span>' +
      "</div>";
    chatWindow.appendChild(row);
    scrollToBottom();
  }

  function removeTyping() {
    const el = document.getElementById("typing-indicator");
    if (el) el.remove();
  }
// -------------------------------------------------------------- behaviour
  async function sendMessage(preset) {
    const message = (preset || messageInput.value).trim();
    if (!message || busy) return;

    addMessage("user", message);
    messageInput.value = "";
    autosize();
    suggestions.hidden = true;
    setBusy(true);
    addTyping();

    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: message,
          conversation_id: conversationId,
          customer_email: emailInput.value.trim() || null,
        }),
      });

      const data = await response.json().catch(() => ({}));
      removeTyping();

      if (!response.ok) {
        addMessage("assistant", data.error || "Something went wrong on the server.", { error: true });
        return;
      }

      conversationId = data.conversation_id;
      storeState();
      addMessage("assistant", data.reply, { intent: data.intent, time: clockTime(), escalated: data.escalated });
    } catch (err) {
      removeTyping();
      addMessage("assistant", "I couldn't reach the server (" + err.message + "). Is the Flask app still running?", {
        error: true,
      });
    } finally {
      setBusy(false);
    }
  }

  function startNewConversation(announce, focusComposer) {
    conversationId = null;
    chatWindow.innerHTML = "";
    storeState();

    if (announce) addNote("Started a new conversation.");
    addMessage("assistant", GREETING, { time: clockTime() });

    suggestions.hidden = false;
    messageInput.value = "";
    autosize();
    // Focusing on first load would scroll the page on phones, so only the
    // explicit actions (New chat, sending a reply) move the caret.
    if (focusComposer !== false) messageInput.focus();
  }

  chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  // Enter sends, Shift+Enter adds a line -- the usual convention for a composer.
  messageInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  messageInput.addEventListener("input", autosize);
  emailInput.addEventListener("change", storeState);
  newChatBtn.addEventListener("click", () => startNewConversation(true));

  suggestions.addEventListener("click", (event) => {
    const chip = event.target.closest(".chip");
    if (chip) sendMessage(chip.dataset.prompt);
  });

  // ------------------------------------------------------------------ boot
  const stored = readStoredState();
  if (stored.email) emailInput.value = stored.email;
  const restoredConversationId = stored.conversationId || null;

  startNewConversation(false, false);
  if (restoredConversationId) {
    conversationId = restoredConversationId;
    addNote("Continuing your earlier conversation #" + restoredConversationId + ".");
  }
})();
