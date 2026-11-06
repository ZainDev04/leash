// Runs before the page paints, so the saved theme never flashes the wrong colors
try { document.documentElement.dataset.theme = localStorage.getItem("leash-theme-v2") === "dark" ? "dark" : "light"; }
catch { document.documentElement.dataset.theme = "light"; }
