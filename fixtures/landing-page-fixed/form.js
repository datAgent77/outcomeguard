// Fixed: posts the form to the configured verification sink and shows a confirmation.
document.getElementById("contact-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = document.getElementById("status");
  try {
    const res = await fetch(window.OG_SINK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(new FormData(form)),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    status.textContent = "Thank you — we'll be in touch.";
    form.reset();
  } catch (err) {
    status.textContent = "Something went wrong. Please try again.";
  }
});
