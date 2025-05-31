let stage = 0;
let accountId = "";
let phone = "";
let queryType = "";

function sendMessage() {
  const input = document.getElementById("user-input").value.trim();
  document.getElementById("chat-box").innerHTML += `<p><b>You:</b> ${input}</p>`;

  if (stage === 0) {
    queryType = input === "1" ? "emi" : input === "2" ? "balance" : "loan";
    appendBot("Please enter your account ID:");
    stage = 1;

  } else if (stage === 1) {
    accountId = input;
    fetch("/send_otp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ account_id: accountId })
    })
    .then(r => r.json())
    .then(data => {
      if (data.status === "success") {
        phone = data.phone;
        appendBot("OTP sent to your registered number. Enter OTP:");
        stage = 2;
      } else {
        appendBot(data.message);
      }
    });

  } else if (stage === 2) {
    const otp = input;
    fetch("/verify_otp", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone: phone, otp: otp })
    })
    .then(r => r.json())
    .then(data => {
      if (data.status === "success") {
        appendBot("OTP verified! Fetching your information...");
        stage = 3;
        showLoader(true);

        fetch("/query", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ account_id: accountId, query_type: queryType })
        })
        .then(r => r.json())
        .then(data => {
          showLoader(false);
          if (data.status === "success") {
            appendBot(data.reply);
          } else {
            appendBot(data.message);
          }
        });

      } else {
        appendBot(data.message);
      }
    });
  }

  document.getElementById("user-input").value = "";
}

function appendBot(msg) {
  document.getElementById("chat-box").innerHTML += `<p><b>Bot:</b> ${msg}</p>`;
}

function showLoader(show) {
  document.getElementById("loader").className = show ? "" : "hidden";
}
