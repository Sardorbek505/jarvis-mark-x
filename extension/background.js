// JARVIS Media Bridge — Background Service Worker
let socket = null;
let reconnectInterval = 3000;
// Порт сервера моста: core/media/bridge/server.py (BrowserBridgeServer, 18765)
const SERVER_URL = "ws://127.0.0.1:18765";

function connectWebSocket() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  try {
    socket = new WebSocket(SERVER_URL);

    socket.onopen = () => {
      console.log("[JARVIS Bridge] Connected to Jarvis Bridge Server at " + SERVER_URL);
      // При подключении запрашиваем состояние всех вкладок с медиа
      broadcastStateRequest();
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        handleJarvisCommand(message);
      } catch (err) {
        console.error("[JARVIS Bridge] Error parsing message:", err);
      }
    };

    socket.onclose = () => {
      console.log("[JARVIS Bridge] Socket closed. Retrying in " + (reconnectInterval / 1000) + "s...");
      setTimeout(connectWebSocket, reconnectInterval);
    };

    socket.onerror = (err) => {
      console.error("[JARVIS Bridge] Socket error:", err);
    };
  } catch (err) {
    console.error("[JARVIS Bridge] Connection failed:", err);
    setTimeout(connectWebSocket, reconnectInterval);
  }
}

function sendToJarvis(payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(payload));
  }
}

function handleJarvisCommand(msg) {
  const { command, tab_id, ...params } = msg;

  if (tab_id) {
    // Отправляем конкретной вкладке tab_id
    chrome.tabs.sendMessage(parseInt(tab_id, 10), { command, ...params }, (response) => {
      if (chrome.runtime.lastError) {
        sendToJarvis({ type: "COMMAND_RESPONSE", success: false, tab_id, error: chrome.runtime.lastError.message });
      } else {
        sendToJarvis({ type: "COMMAND_RESPONSE", success: true, tab_id, response });
      }
    });
  } else {
    // Если tab_id не указан, отправляем во все медиа-вкладки
    chrome.tabs.query({}, (tabs) => {
      tabs.forEach((tab) => {
        chrome.tabs.sendMessage(tab.id, { command, ...params }, () => {
          // Игнорируем ошибки для вкладок без медиа
          if (!chrome.runtime.lastError) {}
        });
      });
    });
  }
}

function broadcastStateRequest() {
  chrome.tabs.query({}, (tabs) => {
    tabs.forEach((tab) => {
      chrome.tabs.sendMessage(tab.id, { command: "get_state" }, (response) => {
        if (!chrome.runtime.lastError && response && response.state) {
          sendToJarvis({
            type: "MEDIA_STATE_UPDATE",
            tab_id: String(tab.id),
            window_id: String(tab.windowId),
            url: tab.url,
            title: tab.title,
            state: response.state
          });
        }
      });
    });
  });
}

// Принимаем обновления от content.js
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg.type === "MEDIA_STATE_UPDATE" && sender.tab) {
    sendToJarvis({
      type: "MEDIA_STATE_UPDATE",
      tab_id: String(sender.tab.id),
      window_id: String(sender.tab.windowId),
      url: sender.tab.url || msg.state.url,
      title: sender.tab.title || msg.state.title,
      reason: msg.reason,
      state: msg.state
    });
  }
});

// Запускаем подключение
connectWebSocket();
