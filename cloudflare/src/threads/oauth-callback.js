export function threadsOAuthCallbackResponse(request, redirectUri, profile) {
  const payload = {
    connected: true,
    profile,
  };
  if (request.headers.get("accept")?.includes("application/json")) {
    return Response.json(payload, { headers: { "cache-control": "no-store" } });
  }
  const message = JSON.stringify({
    type: "threads-oauth-complete",
    status: "connected",
    profile_id: profile.profile_id,
    username: profile.username,
  }).replaceAll("<", "\\u003c");
  const origin = JSON.stringify(new URL(redirectUri).origin);
  const html = `<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>Threads 연결 완료</title></head>
<body><p>Threads 프로필 연결을 완료했습니다. 이 창을 닫아도 됩니다.</p>
<script>window.opener?.postMessage(${message},${origin});window.close();</script></body></html>`;
  return new Response(html, {
    headers: {
      "cache-control": "no-store",
      "content-security-policy": "default-src 'none'; script-src 'unsafe-inline'",
      "content-type": "text/html; charset=utf-8",
    },
  });
}
