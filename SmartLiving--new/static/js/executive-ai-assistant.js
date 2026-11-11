(function () {
  function ready(fn) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', fn, { once: true });
    } else {
      fn();
    }
  }

  ready(function () {
    var root = document.querySelector('[data-exec-ai-assistant]');
    if (!root) return;

    var endpoint = root.getAttribute('data-endpoint') || '/api/ai/chat';
    var panel = root.querySelector('[data-ai-panel]');
    var launcher = root.querySelector('[data-ai-launcher]');
    var closeBtn = root.querySelector('[data-ai-close]');
    var messagesEl = root.querySelector('[data-ai-messages]');
    var inputEl = root.querySelector('[data-ai-input]');
    var sendBtn = root.querySelector('[data-ai-send]');
    var statusEl = root.querySelector('[data-ai-status]');
    var chips = Array.prototype.slice.call(root.querySelectorAll('[data-ai-chip]'));
    var conversation = [];
    var pending = false;
    var typingBubble = null;

    function setOpen(open) {
      panel.setAttribute('data-open', open ? 'true' : 'false');
      if (open) inputEl.focus();
    }

    function setPending(nextPending) {
      pending = nextPending;
      inputEl.disabled = nextPending;
      sendBtn.disabled = nextPending;
      statusEl.textContent = '';
      statusEl.classList.remove('error');
      if (nextPending) {
        showTypingBubble();
      } else {
        removeTypingBubble();
      }
    }

    function appendMessage(role, content, meta) {
      var bubble = document.createElement('div');
      bubble.className = 'exec-ai-message ' + role;
      bubble.textContent = content;
      if (meta) {
        var metaEl = document.createElement('div');
        metaEl.className = 'exec-ai-meta';
        metaEl.textContent = meta;
        bubble.appendChild(metaEl);
      }
      messagesEl.appendChild(bubble);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function setError(message) {
      statusEl.textContent = message;
      statusEl.classList.add('error');
    }

    function buildTypingDots() {
      var wrap = document.createElement('div');
      wrap.className = 'exec-ai-typing';
      for (var i = 0; i < 3; i += 1) {
        var dot = document.createElement('span');
        dot.className = 'exec-ai-typing-dot';
        wrap.appendChild(dot);
      }
      return wrap;
    }

    function showTypingBubble() {
      if (typingBubble) return;
      typingBubble = document.createElement('div');
      typingBubble.className = 'exec-ai-message assistant';
      typingBubble.setAttribute('data-ai-typing', 'true');
      typingBubble.appendChild(buildTypingDots());
      messagesEl.appendChild(typingBubble);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function removeTypingBubble() {
      if (!typingBubble) return;
      typingBubble.remove();
      typingBubble = null;
    }

    async function sendMessage(message) {
      var text = String(message || '').trim();
      if (!text || pending) return;
      setOpen(true);
      appendMessage('user', text);
      conversation.push({ role: 'user', content: text });
      inputEl.value = '';
      setPending(true);

      var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
      var timeoutId = controller ? window.setTimeout(function () {
        controller.abort();
      }, 90000) : null;

      try {
        var response = await fetch(endpoint, {
          method: 'POST',
          credentials: 'same-origin',
          signal: controller ? controller.signal : undefined,
          headers: {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
          },
          body: JSON.stringify({
            message: text,
            conversation: conversation.slice(-8)
          })
        });
        var data = {};
        try {
          data = await response.json();
        } catch (err) {
          data = {};
        }

        if (!response.ok || !data.ok || !data.answer) {
          throw new Error(
            data.error ||
            data.message ||
            (response.status === 403 ? 'You are not allowed to use CYBOK.' :
              response.status === 429 ? 'Too many AI requests. Please wait a moment.' :
                'CYBOK could not complete the request.')
          );
        }

        var meta = Array.isArray(data.data_used) && data.data_used.length
          ? 'Data used: ' + data.data_used.join(', ')
          : '';
        appendMessage('assistant', data.answer, meta);
        conversation.push({ role: 'assistant', content: data.answer });
      } catch (error) {
        var errorMessage = 'CYBOK request failed.';
        if (error && error.name === 'AbortError') {
          errorMessage = 'CYBOK request timed out. Please try again.';
        } else {
          errorMessage = error instanceof Error ? error.message : 'CYBOK request failed.';
        }
        setError(errorMessage);
        appendMessage('assistant', errorMessage, 'Request failed');
      } finally {
        if (timeoutId) window.clearTimeout(timeoutId);
        setPending(false);
      }
    }

    launcher.addEventListener('click', function () {
      setOpen(panel.getAttribute('data-open') !== 'true');
    });
    closeBtn.addEventListener('click', function () { setOpen(false); });
    sendBtn.addEventListener('click', function () { sendMessage(inputEl.value); });
    inputEl.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage(inputEl.value);
      }
    });
    chips.forEach(function (chip) {
      chip.addEventListener('click', function () {
        sendMessage(chip.getAttribute('data-ai-chip') || chip.textContent || '');
      });
    });

    appendMessage(
      'assistant',
      'Ask for sales, customer, agent, inventory, or fulfillment summaries. I only answer from safe CRM analytics summaries.',
      null
    );
  });
})();
