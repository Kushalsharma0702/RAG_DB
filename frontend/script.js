// script.js
let stage = 0;
let chatHistory = [];
let pendingMessage = "";
let checkingForAgentMessages = false;
let lastMessageCheckTime = new Date().toISOString();
const socket = io();

window.onload = () => {
  const userInput = document.getElementById("user-input");
  userInput.addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = this.scrollHeight + "px";
  });

  setTimeout(() => {
    addMessage("Hello! I am your financial assistant. You can ask me about your EMI, account balance, or loan details. You can also select an option below.", 'bot');
    showOptions();
  }, 1000);

  userInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleUserInput();
    }
  });

  if (localStorage.getItem('authenticated') === 'true') {
    startCheckingForAgentMessages();
  }
};

function startCheckingForAgentMessages() {
  if (checkingForAgentMessages) return;
  checkingForAgentMessages = true;
  setInterval(checkForAgentMessages, 3000);
}

async function checkForAgentMessages() {
  if (!localStorage.getItem('authenticated') || !localStorage.getItem('customer_id')) return;

  try {
    const response = await fetch('/check_agent_messages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        customer_id: localStorage.getItem('customer_id'),
        last_check_time: lastMessageCheckTime
      })
    });

    if (!response.ok) return;

    const data = await response.json();
    if (data.messages && data.messages.length > 0) {
      lastMessageCheckTime = new Date().toISOString();
      data.messages.forEach(message => addAgentMessage(message.message_text, message.timestamp));
    }
  } catch (error) {
    console.error('Error checking for agent messages:', error);
  }
}

function addAgentMessage(message, timestamp) {
  const chatBox = document.getElementById('chat-box');
  const messageElement = document.createElement('p');
  messageElement.className = 'agent';
  messageElement.innerHTML = `
    <span class="agent-badge">Live Agent</span>
    <div class="message-content">${message}</div>
    <div class="message-time">${formatTime(new Date(timestamp))}</div>
  `;
  chatBox.appendChild(messageElement);
  chatBox.scrollTop = chatBox.scrollHeight;
  chatHistory.push({ sender: 'agent', content: message, timestamp });
}

function formatTime(date) {
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function showOptions() {
  const options = [
    { text: "My EMI", value: "emi" },
    { text: "My Account Balance", value: "balance" },
    { text: "My Loan Amount", value: "loan" }
  ];
  const chatBox = document.getElementById("chat-box");
  const container = document.createElement("div");
  container.className = "options-container";
  options.forEach(opt => {
    const btn = document.createElement("button");
    btn.className = "option-button";
    btn.textContent = opt.text;
    btn.onclick = () => handleOption(opt.value, opt.text);
    container.appendChild(btn);
  });
  chatBox.appendChild(container);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function handleOption(value, label) {
  pendingMessage = value;
  addMessage(label, 'user');
  removeOptions();
  addMessage("Understood. To proceed, please enter your Account ID:", 'bot');
  stage = 1;
}

function removeOptions() {
  document.querySelector(".options-container")?.remove();
}

async function handleUserInput() {
  const inputField = document.getElementById("user-input");
  const input = inputField.value.trim();
  if (!input) return;

  addMessage(input, 'user');
  inputField.value = "";
  inputField.style.height = "auto";

  if (stage === 0) {
    pendingMessage = input;
    removeOptions();
    addMessage("Understood. To proceed, please enter your Account ID:", 'bot');
    stage = 1;
  } else if (stage === 1) {
    const accountId = input;
    showLoader(true);
    try {
      const response = await fetch("/send_otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: accountId })
      });
      const data = await response.json();
      addMessage(data.message || "An unknown error occurred.", 'bot');
      if (response.ok) stage = 2;
    } catch (err) {
      console.error("Error sending OTP:", err);
      addMessage("A network error occurred while sending the OTP.", 'bot');
    } finally {
      showLoader(false);
    }
  } else if (stage === 2) {
    const otp = input;
    showLoader(true);
    try {
      const response = await fetch("/verify_otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ otp })
      });
      const data = await response.json();
      addMessage(data.message || "An unknown error occurred.", 'bot');
      if (response.ok) {
        stage = 3;
        await processChat(pendingMessage);
        handleOtpVerificationSuccess(data);
      }
    } catch (err) {
      console.error("Error verifying OTP:", err);
      addMessage("A network error occurred while verifying the OTP.", 'bot');
    } finally {
      showLoader(false);
    }
  } else if (stage === 3) {
    await processChat(input);
  }
}

async function handleOtpVerificationSuccess(data) {
  localStorage.setItem('authenticated', 'true');
  localStorage.setItem('customer_id', data.customer_id);
  socket.emit('join_customer_room', { customer_id: data.customer_id });
  startCheckingForAgentMessages();
}

async function processChat(message) {
  showLoader(true);
  try {
    const response = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        chat_history: chatHistory.map(e => ({ sender: e.sender, content: e.content }))
      })
    });
    const data = await response.json();

    if (response.ok) {
      addMessage(data.reply, 'bot', true);
      if (data.needs_agent) {
        addMessage("Would you like me to connect you with a live agent?", 'bot');
        showAgentConnectOptions();
      } else {
        showFeedbackButtons();
      }
    } else {
      addMessage("Sorry, I couldn't fetch that information.", 'bot');
      showAgentConnectOptions();
    }
  } catch (err) {
    console.error("Error processing chat:", err);
    addMessage("A network error occurred.", 'bot');
    showAgentConnectOptions();
  } finally {
    showLoader(false);
  }
}

function addMessage(msg, sender, isMarkdown = false) {
  const chatBox = document.getElementById("chat-box");
  const message = document.createElement("p");
  message.className = sender;
  message.innerHTML = isMarkdown && sender === 'bot' ? marked.parse(msg) : msg;
  chatBox.appendChild(message);
  chatBox.scrollTop = chatBox.scrollHeight;
  chatHistory.push({ sender, content: msg });
}

function showLoader(show) {
  document.getElementById("loader").style.display = show ? "block" : "none";
}

function showAgentConnectOptions() {
  const chatBox = document.getElementById("chat-box");
  const container = document.createElement("div");
  container.className = "options-container";

  const yesBtn = document.createElement("button");
  yesBtn.className = "option-button";
  yesBtn.textContent = "Yes, connect with agent";
  yesBtn.onclick = () => handleAgentConnect(true);

  const noBtn = document.createElement("button");
  noBtn.className = "option-button";
  noBtn.textContent = "No, continue with bot";
  noBtn.onclick = () => handleAgentConnect(false);

  container.appendChild(yesBtn);
  container.appendChild(noBtn);
  chatBox.appendChild(container);
  chatBox.scrollTop = chatBox.scrollHeight;
}

async function handleAgentConnect(connect) {
  removeOptions();
  if (connect) {
    addMessage("Connecting you with an agent...", 'bot');
    showLoader(true);
    try {
      await fetch("/connect_agent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_history: chatHistory })
      });
      addMessage("An agent will assist you shortly.", 'bot');
    } catch (err) {
      console.error("Agent connection error:", err);
      addMessage("Could not connect to agent. Please try again later.", 'bot');
    } finally {
      showLoader(false);
      resetToInitialState();
    }
  } else {
    addMessage("Okay, let me know if you need anything else.", 'bot');
    resetToInitialState();
  }
}

function showFeedbackButtons() {
  document.querySelector(".feedback-container")?.remove();
  const chatBox = document.getElementById("chat-box");
  const container = document.createElement("div");
  container.className = "feedback-container";

  const thumbsUp = document.createElement("button");
  thumbsUp.className = "feedback-button positive";
  thumbsUp.innerHTML = "<i class='fas fa-thumbs-up'></i>";
  thumbsUp.onclick = () => sendFeedback(true);

  const thumbsDown = document.createElement("button");
  thumbsDown.className = "feedback-button negative";
  thumbsDown.innerHTML = "<i class='fas fa-thumbs-down'></i>";
  thumbsDown.onclick = () => sendFeedback(false);

  container.appendChild(thumbsUp);
  container.appendChild(thumbsDown);
  chatBox.appendChild(container);
  chatBox.scrollTop = chatBox.scrollHeight;
}

async function sendFeedback(isUseful) {
  document.querySelector(".feedback-container")?.remove();
  addMessage("Thank you for your feedback!", 'bot');

  if (!isUseful) {
    addMessage("Your conversation has been logged for review.", 'bot');
    try {
      await fetch("/summarize_chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_history: chatHistory })
      });
    } catch (err) {
      console.error("Summary error:", err);
    }
  }
  resetToInitialState();
}

function resetToInitialState() {
  setTimeout(() => {
    addMessage("Is there anything else I can help you with?", 'bot');
    stage = 3;
    pendingMessage = "";
    showOptions();
  }, 1500);
}

// WebSocket event
socket.on('new_message', function (data) {
  if (data.sender === 'agent') {
    addAgentMessage(data.message, data.timestamp);
  }
});
