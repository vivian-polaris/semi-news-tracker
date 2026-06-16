// GET /api/borrow
// Tries TWSE borrow endpoints; falls back to T86 chips data if all blocked.
// Returns standardized JSON for VCPfinder borrow display.

const CORS = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 's-maxage=900',
};

const HDR = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Accept': 'application/json, text/plain, */*',
  'Accept-Language': 'zh-TW,zh;q=0.9',
  'Referer': 'https://www.twse.com.tw/',
};

function twDate() {
  const tw = new Date(Date.now() + 8 * 3600000);
  return tw.toISOString().slice(0, 10).replace(/-/g, '');
}

export async function onRequestGet() {
  const d = twDate();

  // ── 1. MI_SLBK JSON (full market borrow balance) ─────────────────────────
  for (const url of [
    `https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?response=json&selectType=AL&date=${d}`,
    'https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?response=json&selectType=AL',
  ]) {
    try {
      const r = await fetch(url, { headers: HDR });
      const text = await r.text();
      if (text?.trim().startsWith('{')) {
        const j = JSON.parse(text);
        if (j.stat === 'OK' && j.data?.length > 0)
          return new Response(JSON.stringify({ ...j, _src: 'MI_SLBK' }), { headers: CORS });
      }
    } catch (_) {}
  }

  // ── 2. BFT41U JSON (top 20 borrow) ───────────────────────────────────────
  for (const url of [
    `https://www.twse.com.tw/rwd/zh/fund/BFT41U?response=json&date=${d}`,
    'https://www.twse.com.tw/rwd/zh/fund/BFT41U?response=json',
  ]) {
    try {
      const r = await fetch(url, { headers: HDR });
      const text = await r.text();
      if (text?.trim().startsWith('{')) {
        const j = JSON.parse(text);
        if (j.stat === 'OK' && j.data?.length > 0)
          return new Response(JSON.stringify({ ...j, _src: 'BFT41U' }), { headers: CORS });
      }
    } catch (_) {}
  }

  // ── 3. T86 (三大法人買賣超) as short-pressure proxy ──────────────────────
  // foreignNet + trustNet < 0 → institutional selling pressure similar to short
  for (const url of [
    `https://www.twse.com.tw/rwd/zh/fund/T86?date=${d}&selectType=ALL&response=json`,
    `https://www.twse.com.tw/rwd/zh/fund/T86?date=${String(Number(d)-1)}&selectType=ALL&response=json`,
  ]) {
    try {
      const r = await fetch(url, { headers: HDR });
      const text = await r.text();
      if (!text?.trim().startsWith('{')) continue;
      const j = JSON.parse(text);
      if ((j.stat === 'OK' || j.stat === '成功') && j.data?.length > 0) {
        // Convert to borrow-like format: negative net = short pressure
        // T86 fields: 代號, 名稱, ...外陸資(idx4), ...投信(idx10), ...自營商(idx13)
        const flds = j.fields || [];
        let fi = 4, ti = 10, di = 13, ci = 0, ni = 1;
        flds.forEach((f, i) => {
          if (f.includes('外陸資買賣超') || f.includes('外資買賣超股數')) fi = i;
          else if (f.includes('投信買賣超')) ti = i;
          else if (f.includes('自營商買賣超')) di = i;
        });
        const pn = s => parseInt((s || '0').replace(/,/g, '')) || 0;
        // Simulate borrow-style: "balance" = absolute institutional sell (in 千股)
        const data = j.data
          .map(row => {
            const code = (row[ci] || '').trim();
            const name = (row[ni] || '').trim();
            const foreign = pn(row[fi]);
            const trust   = pn(row[ti]);
            const sell    = -(foreign + trust); // positive = selling
            return { code, name, sell, foreign, trust };
          })
          .filter(r => /^\d{4,6}$/.test(r.code) && r.sell > 0)
          .sort((a, b) => b.sell - a.sell)
          .slice(0, 50);

        // Return in MI_SLBK-like format so frontend can parse
        const rows = data.map(r => [r.code, r.name, String(r.sell), '0', String(r.sell), '0']);
        return new Response(JSON.stringify({
          stat: 'OK', date: j.date || d, _src: 'T86',
          _note: '外資+投信賣超張數（借券替代指標）',
          fields: ['代號', '名稱', '賣超(千股)', '', '賣出壓力(千股)', ''],
          data: rows,
        }), { headers: CORS });
      }
    } catch (_) {}
  }

  return new Response(JSON.stringify({ stat: 'FAIL', data: [], _src: 'all-failed' }), { headers: CORS });
}
