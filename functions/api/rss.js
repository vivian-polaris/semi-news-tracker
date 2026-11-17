// GET /api/rss?url=<encoded RSS url>
// 伺服器端抓 RSS，取代不穩定的免費 CORS proxy。僅允許白名單網域，避免被當開放代理。

const ALLOW = ['news.google.com'];

export async function onRequestGet({ request }) {
  const target = new URL(request.url).searchParams.get('url') || '';
  let u;
  try { u = new URL(target); } catch (_) { return new Response('bad url', { status: 400 }); }
  if (u.protocol !== 'https:' || !ALLOW.includes(u.hostname)) {
    return new Response('forbidden host', { status: 403 });
  }
  try {
    const r = await fetch(u.toString(), {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*',
      },
      cf: { cacheTtl: 300, cacheEverything: true },
    });
    if (!r.ok) return new Response('upstream ' + r.status, { status: 502 });
    return new Response(await r.text(), {
      headers: {
        'Content-Type': 'application/xml; charset=utf-8',
        'Cache-Control': 'public, max-age=300',
      },
    });
  } catch (e) {
    return new Response('fetch failed: ' + e.message, { status: 502 });
  }
}
