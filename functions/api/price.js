// GET /api/price?syms=2330.TW,2303.TW,6488.TWO
// Proxies TWSE mis.twse.com.tw real-time API (server-side, no CORS/rate-limit issues)
// Returns: {"2330": 850.0, "2303": 55.2, ...}

const CORS = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 's-maxage=10',  // Cloudflare edge caches 10s — shorter window for intraday accuracy
};

export async function onRequestGet(context) {
  const url = new URL(context.request.url);
  const symsParam = url.searchParams.get('syms') || '';
  if (!symsParam) return new Response('{}', { headers: CORS });

  const syms = symsParam.split(',').map(s => s.trim()).filter(Boolean).slice(0, 150);
  if (!syms.length) return new Response('{}', { headers: CORS });

  // Build TWSE ex_ch: tse_2330.tw|otc_6488.tw|...
  const exCh = syms.map(sym => {
    const [code, ex] = sym.split('.');
    return `${ex === 'TWO' ? 'otc' : 'tse'}_${code}.tw`;
  }).join('|');

  const twseUrl = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp` +
    `?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0&_=${Date.now()}`;

  try {
    const resp = await fetch(twseUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://mis.twse.com.tw/stock/fibest.jsp',
        'Accept': 'application/json',
      },
      cf: { cacheTtl: 15 },  // Cloudflare cache
    });
    if (!resp.ok) throw new Error('TWSE ' + resp.status);
    const data = await resp.json();

    const prices = {};
    (data.msgArray || []).forEach(item => {
      if (!item.c) return;
      // Only return z (current transaction price). Never fall back to y (yesterday's close)
      // — returning y would silently cache stale data as "current price" in the browser.
      const z = parseFloat(item.z);
      if (z > 0) prices[item.c] = z;
    });

    return new Response(JSON.stringify(prices), { headers: CORS });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 502, headers: CORS });
  }
}
