// VCP Price Updater — Cloudflare Worker Cron
// 每2分鐘（盤中）抓全市場即時成交價，存入 KV
// Browser 的 /api/price 讀 KV，不再每次打 TWSE

const PAGES_BASE = 'https://vivian-vcpfinder.pages.dev';
const TWSE_BATCH = 100;    // 每次送 TWSE 的股數
const TWSE_PARALLEL = 2;   // 同時發出的批次數
const KV_PRICES_KEY = 'live_prices';
const KV_TS_KEY = 'live_prices_ts';

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(updatePrices(env));
  },
};

async function updatePrices(env) {
  // 1. 讀股票代號清單（輕量，49KB）
  let codes = [];
  try {
    const r = await fetch(`${PAGES_BASE}/stock_codes.json?_=${Date.now()}`, {
      cf: { cacheTtl: 300 },
    });
    if (r.ok) codes = await r.json();
  } catch (_) {}

  if (!codes.length) return;

  const prices = {};

  // 2. 分批送 TWSE 即時 API（每批 100 支，2 批並行）
  const batches = [];
  for (let i = 0; i < codes.length; i += TWSE_BATCH) {
    batches.push(codes.slice(i, i + TWSE_BATCH));
  }

  for (let i = 0; i < batches.length; i += TWSE_PARALLEL) {
    const chunk = batches.slice(i, i + TWSE_PARALLEL);
    await Promise.all(chunk.map(batch => fetchTWSEBatch(batch, prices)));
    if (i + TWSE_PARALLEL < batches.length) {
      await sleep(150);
    }
  }

  // 3. TWSE 沒拿到的補用 Yahoo Finance（server-side，無 CORS 問題）
  const missing = codes.filter(s => !prices[s.c]);
  if (missing.length > 0) {
    const yfBatches = [];
    for (let i = 0; i < missing.length; i += 100) {
      yfBatches.push(missing.slice(i, i + 100));
    }
    for (let i = 0; i < yfBatches.length; i += 2) {
      const chunk = yfBatches.slice(i, i + 2);
      await Promise.all(chunk.map(b => fetchYahooBatch(b, prices)));
      if (i + 2 < yfBatches.length) await sleep(200);
    }
  }

  // 4. 寫入 KV（TTL 1小時，盤後過期）
  const count = Object.keys(prices).length;
  if (count > 0) {
    await Promise.all([
      env.VCP_KV.put(KV_PRICES_KEY, JSON.stringify(prices), { expirationTtl: 3600 }),
      env.VCP_KV.put(KV_TS_KEY, String(Date.now()), { expirationTtl: 3600 }),
    ]);
  }
}

async function fetchTWSEBatch(batch, prices) {
  const exCh = batch
    .map(s => `${s.ex === 'TWO' ? 'otc' : 'tse'}_${s.c}.tw`)
    .join('|');
  const url =
    `https://mis.twse.com.tw/stock/api/getStockInfo.jsp` +
    `?ex_ch=${encodeURIComponent(exCh)}&json=1&delay=0&_=${Date.now()}`;
  try {
    const r = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://mis.twse.com.tw/stock/fibest.jsp',
        'Accept': 'application/json',
      },
    });
    if (!r.ok) return;
    const data = await r.json();
    (data.msgArray || []).forEach(item => {
      if (!item.c) return;
      const z = parseFloat(item.z);
      if (z > 0) prices[item.c] = z;
    });
  } catch (_) {}
}

async function fetchYahooBatch(batch, prices) {
  const syms = batch.map(s => `${s.c}.${s.ex}`).join(',');
  const url =
    `https://query1.finance.yahoo.com/v7/finance/quote` +
    `?symbols=${encodeURIComponent(syms)}&fields=regularMarketPrice&_=${Date.now()}`;
  try {
    const r = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; VCPBot/1.0)',
        'Accept': 'application/json',
      },
    });
    if (!r.ok) return;
    const j = await r.json();
    (j?.quoteResponse?.result || []).forEach(q => {
      if (q?.regularMarketPrice > 0 && q?.symbol) {
        const code = q.symbol.split('.')[0];
        if (!prices[code]) prices[code] = q.regularMarketPrice;
      }
    });
  } catch (_) {}
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}
