// GET /api/price?syms=2330.TW,2303.TW,6488.TWO
// 1. 先讀 Cloudflare KV（由 vcp-price-updater Worker 每2分鐘更新）
// 2. KV 無資料或過舊（>3分鐘），fallback 直接打 TWSE
// Returns: {"2330": 850.0, "2303": 55.2, ...}

const CORS = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 's-maxage=10',
};

const KV_MAX_AGE_MS = 3 * 60 * 1000; // KV 資料超過 3 分鐘視為過舊

export async function onRequestGet(context) {
  const { env, request } = context;
  const url = new URL(request.url);
  const symsParam = url.searchParams.get('syms') || '';
  if (!symsParam) return new Response('{}', { headers: CORS });

  const syms = symsParam.split(',').map(s => s.trim()).filter(Boolean).slice(0, 150);
  if (!syms.length) return new Response('{}', { headers: CORS });

  // ── 1. 先讀 KV（cron Worker 每2分鐘更新，最多差2分鐘）──────────────────
  try {
    const [pricesRaw, tsRaw] = await Promise.all([
      env.VCP_KV.get('live_prices'),
      env.VCP_KV.get('live_prices_ts'),
    ]);
    if (pricesRaw && tsRaw) {
      const age = Date.now() - parseInt(tsRaw);
      if (age < KV_MAX_AGE_MS) {
        const allPrices = JSON.parse(pricesRaw);
        const prices = {};
        syms.forEach(sym => {
          const code = sym.split('.')[0];
          if (allPrices[code] > 0) prices[code] = allPrices[code];
        });
        // KV 有資料時直接回傳，不打 TWSE
        return new Response(JSON.stringify(prices), { headers: CORS });
      }
    }
  } catch (_) {}

  // ── 2. KV 無資料或過舊 → fallback：直接打 TWSE ─────────────────────────
  const exCh = syms.map(sym => {
    const [code, ex] = sym.split('.');
    return `${ex === 'TWO' ? 'otc' : 'tse'}_${code}.tw`;
  }).join('|');

  const twseUrl =
    `https://mis.twse.com.tw/stock/api/getStockInfo.jsp` +
    `?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0&_=${Date.now()}`;

  try {
    const resp = await fetch(twseUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://mis.twse.com.tw/stock/fibest.jsp',
        'Accept': 'application/json',
      },
      cf: { cacheTtl: 10 },
    });
    if (!resp.ok) throw new Error('TWSE ' + resp.status);
    const data = await resp.json();

    const prices = {};
    (data.msgArray || []).forEach(item => {
      if (!item.c) return;
      const z = parseFloat(item.z);
      if (z > 0) prices[item.c] = z;
    });

    return new Response(JSON.stringify(prices), { headers: CORS });
  } catch (e) {
    return new Response(JSON.stringify({ error: e.message }), { status: 502, headers: CORS });
  }
}
