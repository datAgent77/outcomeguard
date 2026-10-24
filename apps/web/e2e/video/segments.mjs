// Shared by build-video.mjs (silent, captioned segments) and mux-voice.mjs (adds the voice-over).
// `say` is the narration text for each voice file voice/<id>.mp3 — keep in sync with
// docs/hackathon/submission/03c_VOICEOVER_ELEVENLABS.md.
export const WPM = Number(process.env.WPM ?? 150); // expected narrator pace; sets segment length
export const words = (t) => t.trim().split(/\s+/).length;
export const estimate = (t) => (words(t) / WPM) * 60;

export const SEG = [
  { id: "s1", badge: ["", ""], caption: "Hiring a freelancer means paying for a promise.",
    say: "Hiring a freelancer means paying for a promise. Either the buyer pays first and hopes, or the freelancer works first and hopes. OutcomeGuard replaces hope with proof." },
  { id: "s2", badge: ["", ""], caption: "AI drafts · Tools verify · Humans approve · PayPal executes",
    say: "The payment is authorized on PayPal up front, but it is captured only when measurable evidence, and the buyer, say yes. AI drafts, tools verify, humans approve, PayPal executes." },
  { id: "s3", badge: ["LIVE", "live"], caption: "Claude drafts acceptance criteria from plain English → the buyer locks the agreement",
    say: "Acme needs a five-hundred-dollar landing page. The buyer writes the request in plain English, and Claude turns it into measurable acceptance criteria. Each one quotes the buyer's own words. Anything that says \"must\" stays blocking, and the AI's own idea is only a suggestion, off by default. The buyer adds what only they know, and locks the agreement. Locking freezes a content hash, so nobody can quietly move the goalposts." },
  { id: "s4", badge: ["LIVE", "live"], caption: "Real Chromium + Google Lighthouse verify the delivery",
    say: "Now the freelancer delivers. OutcomeGuard opens the page in a real Chromium browser on a phone-sized screen, submits the contact form to a controlled test endpoint, and runs Google Lighthouse. Three checks pass, in plain language, with stored screenshots and the Lighthouse report. Accessibility can't be measured by a tool, so it waits for a human." },
  { id: "s5", badge: ["RECORDED EARLIER", "recorded"], caption: "First delivery: form submitted, success message never appeared → FAIL → capture blocked",
    say: "Here is the first delivery, recorded earlier. The form was submitted, but the success message never appeared. That single failed check blocked the payment." },
  { id: "s6", badge: ["SIMULATED PAYMENT", "simulated"], caption: "Policy re-checked → buyer approves → capture (rehearsal mode: simulated)",
    say: "The accessibility decision is recorded, and labelled in the app as a scripted demo decision. The payment policy is re-evaluated: eligible. The buyer approves, then captures. Two separate, explicit steps. In this recording the payment is simulated, and the app says so." },
  { id: "s7", badge: ["REAL PAYPAL SANDBOX · OCT 16, 2026", "real"], caption: "Captured, refunded with both parties' approval, and cancelled before capture — on PayPal Sandbox",
    say: "And it isn't only a simulation. On PayPal Sandbox, we captured a five-hundred-dollar payment through this exact governance, refunded one hundred after both parties approved, and cancelled an authorization before capture. Every ID was read back from PayPal." },
  { id: "s8", card: true, say: "OutcomeGuard. Don't just pay for a promise. Pay for proof." },
];
