$("login").addEventListener("submit", async e => {
  e.preventDefault();
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  $("error").textContent = "";
  try {
    const r = await fetch("/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: $("password").value }),
    });
    if (r.ok) { location.href = "/app"; return; }
    $("error").textContent = (await r.json().catch(() => ({}))).detail || "Could not log in.";
  } catch {
    $("error").textContent = "Could not reach the server.";
  }
  btn.disabled = false;
});
