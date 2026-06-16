// GET /api/borrow
// Server-side proxy for TWSE borrow balance (MI_SLBK / BFT41U)
// Called from VCPfinder browser to bypass CORS restriction

const CORS = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 's-maxage=1800',
};

const TWSE_HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
  'Accept': 'application/json, text/plain, */*',
  'Accept-Language': 'zh-TW,zh;q=0.9',
  'Referer': 'https://www.twse.com.tw/',
  'Origin': 'https://www.twse.com.tw',
};

export async function onRequestGet() {
  const today = new Date();
  const yyyy = today.getUTCFullYear();
  const mm   = String(today.getUTCMonth() + 1).padStart(2, '0');
  const dd   = String(today.getUTCDate()).padStart(2, '0');
  const dateStr = `${yyyy}${mm}${dd}`;

  const urls = [
    `https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?response=json&selectType=AL&date=${dateStr}`,
    'https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?response=json&selectType=AL',
    `https://www.twse.com.tw/rwd/zh/fund/BFT41U?response=json&date=${dateStr}`,
    'https://www.twse.com.tw/rwd/zh/fund/BFT41U?response=json',
  ];

  for (const url of urls) {
    try {
      const r = await fetch(url, { headers: TWSE_HEADERS });
      if (!r.ok) continue;
      const text = await r.text();
      if (!text || !text.trim()) continue;
      const j = JSON.parse(text);
      if (j.stat === 'OK' && Array.isArray(j.data) && j.data.length > 0) {
        return new Response(JSON.stringify(j), { headers: CORS });
      }
    } catch (_) {}
  }

  return new Response(JSON.stringify({ stat: 'FAIL', data: [] }), { status: 200, headers: CORS });
}
