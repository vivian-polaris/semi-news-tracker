const JSON_HEADERS = {
  'Content-Type': 'application/json; charset=utf-8',
  'Access-Control-Allow-Origin': '*',
  'Cache-Control': 'no-store, no-cache, must-revalidate',
};

const MAX_CODES = 200;
const MAX_HISTORY = 252;

function json(data, init = {}) {
  return new Response(JSON.stringify(data), {
    ...init,
    headers: {
      ...JSON_HEADERS,
      ...(init.headers || {}),
    },
  });
}

function parseCodes(raw) {
  if (!raw) return [];
  return [...new Set(
    raw
      .split(',')
      .map(code => code.trim())
      .filter(code => /^\d{4,6}$/.test(code))
  )];
}

function buildInClause(codes) {
  return codes.map(() => '?').join(',');
}

function normalizeNumber(value) {
  return value == null ? null : Number(value);
}

function rowToFundamental(row) {
  return {
    code: row.code,
    sector: row.sector ?? null,
    pe: normalizeNumber(row.pe),
    pb: normalizeNumber(row.pb),
    div_yield: normalizeNumber(row.div_yield),
    eps: normalizeNumber(row.eps),
    eps_year: row.eps_year ?? null,
    eps_quarter: row.eps_quarter ?? null,
    earnings_growth: normalizeNumber(row.earnings_growth),
    foreign_net: row.foreign_net == null ? null : Number(row.foreign_net),
    trust_net: row.trust_net == null ? null : Number(row.trust_net),
    dealer_net: row.dealer_net == null ? null : Number(row.dealer_net),
    revenue_current: row.revenue_current == null ? null : Number(row.revenue_current),
    revenue_yoy: normalizeNumber(row.revenue_yoy),
    margin_today: row.margin_today == null ? null : Number(row.margin_today),
    margin_change: row.margin_change == null ? null : Number(row.margin_change),
    updated_date: row.updated_date ?? null,
  };
}

export async function onRequestGet({ request, env }) {
  if (!env.VCP_DB) {
    return json({ error: 'D1 binding VCP_DB is not configured.' }, { status: 500 });
  }

  const url = new URL(request.url);

  if (url.searchParams.get('meta') === '1') {
    const metaResult = await env.VCP_DB
      .prepare('SELECT key, value, updated_at FROM market_meta ORDER BY key')
      .all();

    const meta = {};
    for (const row of metaResult.results || []) {
      meta[row.key] = {
        value: row.value,
        updated_at: row.updated_at,
      };
    }

    return json(meta);
  }

  const codes = parseCodes(url.searchParams.get('codes') || '');
  if (!codes.length) {
    return json({ error: 'codes is required.' }, { status: 400 });
  }
  if (codes.length > MAX_CODES) {
    return json({ error: `codes exceeds ${MAX_CODES}.` }, { status: 400 });
  }

  const inClause = buildInClause(codes);

  const pricesSql = `
    WITH ranked AS (
      SELECT
        code,
        date,
        name,
        close,
        high,
        low,
        open,
        volume,
        ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
      FROM stock_prices
      WHERE code IN (${inClause})
    )
    SELECT code, date, name, close, high, low, open, volume
    FROM ranked
    WHERE rn <= ?
    ORDER BY code ASC, date DESC
  `;

  const fundamentalsSql = `
    SELECT
      code,
      sector,
      pe,
      pb,
      div_yield,
      eps,
      eps_year,
      eps_quarter,
      earnings_growth,
      foreign_net,
      trust_net,
      dealer_net,
      revenue_current,
      revenue_yoy,
      margin_today,
      margin_change,
      updated_date
    FROM fundamentals
    WHERE code IN (${inClause})
  `;

  const metaSql = `
    SELECT key, value, updated_at
    FROM market_meta
    WHERE key = 'last_update'
    LIMIT 1
  `;

  const [priceRows, fundamentalRows, metaRows] = await Promise.all([
    env.VCP_DB.prepare(pricesSql).bind(...codes, MAX_HISTORY).all(),
    env.VCP_DB.prepare(fundamentalsSql).bind(...codes).all(),
    env.VCP_DB.prepare(metaSql).all(),
  ]);

  const stocks = {};
  for (const code of codes) {
    stocks[code] = { n: code, c: [], h: [], l: [], v: [] };
  }

  let latestPriceDate = '';
  for (const row of priceRows.results || []) {
    if (!stocks[row.code]) {
      stocks[row.code] = { n: row.code, c: [], h: [], l: [], v: [] };
    }
    const stock = stocks[row.code];
    if (stock.c.length === 0) {
      stock._d = row.date;
      latestPriceDate = latestPriceDate || row.date || '';
    }
    stock.n = row.name || stock.n || row.code;
    stock.c.push(normalizeNumber(row.close));
    stock.h.push(normalizeNumber(row.high));
    stock.l.push(normalizeNumber(row.low));
    stock.v.push(row.volume == null ? null : Number(row.volume));
  }

  const fundamentals = {};
  for (const row of fundamentalRows.results || []) {
    const normalized = rowToFundamental(row);
    fundamentals[row.code] = normalized;
    if (!stocks[row.code]) {
      stocks[row.code] = { n: row.code, c: [], h: [], l: [], v: [] };
    }
    Object.assign(stocks[row.code], {
      sector: normalized.sector,
      pe: normalized.pe,
      pb: normalized.pb,
      divYield: normalized.div_yield,
      eps: normalized.eps,
      epsYear: normalized.eps_year,
      epsQuarter: normalized.eps_quarter,
      earningsGrowth: normalized.earnings_growth,
      foreignNet: normalized.foreign_net,
      trustNet: normalized.trust_net,
      dealerNet: normalized.dealer_net,
      revenueCurrent: normalized.revenue_current,
      revenueYoy: normalized.revenue_yoy,
      marginToday: normalized.margin_today,
      marginChange: normalized.margin_change,
      updatedDate: normalized.updated_date,
    });
  }

  const lastUpdateRow = (metaRows.results || [])[0];
  const priceDate = latestPriceDate || lastUpdateRow?.value || null;

  return json({
    date: priceDate,
    _price_date: priceDate,
    stocks,
    fundamentals,
    meta: lastUpdateRow
      ? {
          last_update: lastUpdateRow.value,
          updated_at: lastUpdateRow.updated_at,
        }
      : {},
  });
}
