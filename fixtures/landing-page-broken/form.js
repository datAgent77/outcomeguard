// Broken: a refactor left a reference to a removed "phone" field. The handler throws
// before any request is sent, so the submission never reaches the sink.
document.getElementById("contact-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.getElementById("status");
  try {
    const phone = form.elements.phone.value.trim(); // TypeError: phone field was removed
    const body = new URLSearchParams(new FormData(form));
    body.set("phone", phone);
    const res = await fetch(window.OG_SINK_URL, { method: "POST", body });
    if (!res.ok) throw new Error("HTTP " + res.status);
    status.textContent = "Thank you — we'll be in touch.";
  } catch (err) {
    status.textContent = "Something went wrong. Please try again.";
  }
});
