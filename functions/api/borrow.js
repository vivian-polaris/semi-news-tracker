// GET /api/borrow
// Server-side proxy for TWSE borrow balance
// Tries MI_SLBK (HTML table parse) then BFT41U as fallback

const CORS = {
  'Content-Type': 'application/json',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 's-maxage=1800',
};

const HDR = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Language': 'zh-TW,zh;q=0.9',
  'Referer': 'https://www.twse.com.tw/zh/page/fund/MI_SLBK.html',
};

// Parse a number string like "1,234" → 1234
function parseNum(s) {
  return parseInt((s || '0').replace(/,/g, '')) || 0;
}

// Attempt to extract table rows from TWSE HTML using regex
function parseHTMLTable(html) {
  const rows = [];
  // Match <tr> blocks
  const trMatches = html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi);
  for (const trMatch of trMatches) {
    const tdMatches = [...trMatch[1].matchAll(/<td[^>]*>([\s\S]*?)<\/td>/gi)];
    const cells = tdMatches.map(m => m[1].replace(/<[^>]+>/g, '').trim());
    if (cells.length >= 5 && /^\d{4,6}$/.test(cells[0])) {
      rows.push(cells);
    }
  }
  return rows;
}

export async function onRequestGet() {
  const today = new Date();
  // Use Taiwan time (UTC+8)
  const tw = new Date(today.getTime() + 8 * 3600000);
  const dateStr = tw.toISOString().slice(0, 10).replace(/-/g, '');

  // ── Strategy 1: MI_SLBK JSON (full market) ───────────────────────────────
  const jsonUrls = [
    `https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?response=json&selectType=AL&date=${dateStr}`,
    'https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?response=json&selectType=AL',
  ];
  for (const url of jsonUrls) {
    try {
      const r = await fetch(url, { headers: { ...HDR, Accept: 'application/json, */*' } });
      const text = await r.text();
      if (text && text.trim().startsWith('{')) {
        const j = JSON.parse(text);
        if (j.stat === 'OK' && j.data?.length > 0) {
          return new Response(JSON.stringify(j), { headers: CORS });
        }
      }
    } catch (_) {}
  }

  // ── Strategy 2: MI_SLBK HTML scrape (full market) ────────────────────────
  const htmlUrls = [
    `https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?selectType=AL&date=${dateStr}`,
    'https://www.twse.com.tw/rwd/zh/fund/MI_SLBK?selectType=AL',
    'https://www.twse.com.tw/zh/fund/MI_SLBK?selectType=AL',
  ];
  for (const url of htmlUrls) {
    try {
      const r = await fetch(url, { headers: HDR });
      const html = await r.text();
      if (!html || html.length < 500) continue;
      const rows = parseHTMLTable(html);
      if (rows.length > 5) {
        // Extract date from HTML
        const dateMatch = html.match(/(\d{3})[\s年/](\d{1,2})[\s月/](\d{1,2})/);
        const dataDate = dateMatch
          ? `${parseInt(dateMatch[1]) + 1911}/${dateMatch[2].padStart(2,'0')}/${dateMatch[3].padStart(2,'0')}`
          : dateStr;
        const data = rows.map(r => r.slice(0, 8));
        return new Response(JSON.stringify({
          stat: 'OK', date: dataDate, _src: 'html',
          fields: ['證券代號','證券名稱','成交量','','借券賣出餘額','借券買進餘額','',''],
          data,
        }), { headers: CORS });
      }
    } catch (_) {}
  }

  // ── Strategy 3: BFT41U JSON (top 20 only) ────────────────────────────────
  const bftUrls = [
    `https://www.twse.com.tw/rwd/zh/fund/BFT41U?response=json&date=${dateStr}`,
    'https://www.twse.com.tw/rwd/zh/fund/BFT41U?response=json',
  ];
  for (const url of bftUrls) {
    try {
      const r = await fetch(url, { headers: { ...HDR, Accept: 'application/json, */*' } });
      const text = await r.text();
      if (text && text.trim().startsWith('{')) {
        const j = JSON.parse(text);
        if (j.stat === 'OK' && j.data?.length > 0) {
          return new Response(JSON.stringify({ ...j, _src: 'BFT41U' }), { headers: CORS });
        }
      }
    } catch (_) {}
  }

  return new Response(JSON.stringify({ stat: 'FAIL', data: [], _src: 'all-failed' }), { headers: CORS });
}
