// Server-rendered pages: the passphrase login and the "not set up yet" notices.
// The library itself is the static app in public/app.

import { escapeHtml as h } from "./util";

export const PAGE_CSP =
  "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; " +
  "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'";

function page(title: string, body: string, status: number): Response {
  const html = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<meta name="color-scheme" content="light dark">
<title>${h(title)}</title>
<link rel="stylesheet" href="/app/app.css">
<link rel="icon" href="/app/icons/icon-192.png">
<link rel="apple-touch-icon" href="/app/icons/apple-touch-icon.png">
<link rel="manifest" href="/app/manifest.webmanifest" crossorigin="use-credentials">
</head>
<body class="page">
<main class="card narrow">
${body}
</main>
</body>
</html>`;
  return new Response(html, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Content-Security-Policy": PAGE_CSP },
  });
}

export function loginPage(libraryTitle: string, error = "", status = 200): Response {
  return page(
    `Sign in · ${libraryTitle}`,
    `<h1 class="brand"><img src="/app/icons/icon-192.png" alt="" width="40" height="40"> ${h(libraryTitle)}</h1>
<form method="post" action="/app/login" class="stack">
  <label for="passphrase">Passphrase</label>
  <input id="passphrase" name="passphrase" type="password" autocomplete="current-password" required autofocus>
  ${error ? `<p class="error" role="alert">${h(error)}</p>` : ""}
  <button type="submit" class="primary">Sign in</button>
</form>
<p class="muted small">This is a private library. Its programs were generated from documents for personal listening.</p>`,
    status,
  );
}

export function noticePage(title: string, paragraphs: string[], status: number): Response {
  return page(title, `<h1>${h(title)}</h1>\n${paragraphs.map((p) => `<p>${p}</p>`).join("\n")}`, status);
}

export function notConfiguredPage(): Response {
  return noticePage(
    "Library not set up yet",
    [
      "The web app's passphrase isn't set. Set the <code>APP_PASSPHRASE</code> secret (10 characters or more) and the <code>UPLOAD_TOKEN</code> secret (32 or more), for example with <code>npx wrangler secret put APP_PASSPHRASE</code>.",
      "To use Cloudflare Access instead, set <code>AUTH_MODE</code> to <code>access</code>. The README in <code>plugin/cloud</code> has the steps.",
    ],
    503,
  );
}

export function accessDeniedPage(reason: string): Response {
  return noticePage(
    "Not signed in through Cloudflare Access",
    [
      h(reason),
      "This library uses Cloudflare Access (<code>AUTH_MODE = \"access\"</code>). Open it through your Access application, or ask its owner to add you to the Access policy.",
    ],
    403,
  );
}
