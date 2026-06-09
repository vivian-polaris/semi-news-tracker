const PRICE_COLS = ['code', 'date', 'name', 'close', 'high', 'low', 'open', 'volume'];
const FUND_COLS = [
  'code', 'sector', 'pe', 'pb', 'div_yield', 'eps', 'eps_year', 'eps_quarter',
  'earnings_growth', 'foreign_net', 'trust_net', 'dealer_net',
  'revenue_current', 'revenue_yoy', 'margin_today', 'margin_change', 'updated_date',
];

// D1 max 100 params per statement: 8 price cols → 12 rows max, 17 fund cols → 5 rows max
const PRICE_ROWS_PER_STMT = 10;  // 10 × 8 = 80 params
const FUND_ROWS_PER_STMT = 5;    // 5 × 17 = 85 params
const STMTS_PER_BATCH = 50;

function buildInsert(db, table, cols, rows) {
  const ph = rows.map(() => `(${cols.map(() => '?').join(',')})`).join(',');
  const sql = `INSERT OR REPLACE INTO ${table} (${cols.join(',')}) VALUES ${ph}`;
  const params = rows.flatMap(row => cols.map(c => row[c] ?? null));
  return db.prepare(sql).bind(...params);
}

async function writeBatched(db, stmts) {
  for (let i = 0; i < stmts.length; i += STMTS_PER_BATCH) {
    await db.batch(stmts.slice(i, i + STMTS_PER_BATCH));
  }
}

function chunk(arr, size) {
  const out = [];
  for (let i = 0; i < arr.length; i += size) out.push(arr.slice(i, i + size));
  return out;
}

export async function onRequestPost({ request, env }) {
  const auth = request.headers.get('Authorization') || '';
  const secret = env.D1_WRITE_SECRET;
  if (!secret || auth !== `Bearer ${secret}`) {
    return new Response('Unauthorized', { status: 401 });
  }

  if (!env.VCP_DB) {
    return new Response(JSON.stringify({ error: 'D1 binding not configured' }), {
      status: 500, headers: { 'Content-Type': 'application/json' },
    });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return new Response(JSON.stringify({ error: 'invalid JSON' }), {
      status: 400, headers: { 'Content-Type': 'application/json' },
    });
  }

  const prices = Array.isArray(body.prices) ? body.prices : [];
  const fundamentals = Array.isArray(body.fundamentals) ? body.fundamentals : [];
  const metaDate = body.meta_date || null;

  const priceStmts = chunk(prices, PRICE_ROWS_PER_STMT).map(rows =>
    buildInsert(env.VCP_DB, 'stock_prices', PRICE_COLS, rows)
  );
  const fundStmts = chunk(fundamentals, FUND_ROWS_PER_STMT).map(rows =>
    buildInsert(env.VCP_DB, 'fundamentals', FUND_COLS, rows)
  );

  const now = new Date().toISOString();
  const metaStmt = env.VCP_DB
    .prepare('INSERT OR REPLACE INTO market_meta (key, value, updated_at) VALUES (?, ?, ?)')
    .bind('last_update', metaDate || now.slice(0, 10), now);

  try {
    await writeBatched(env.VCP_DB, [...priceStmts, ...fundStmts, metaStmt]);
  } catch (err) {
    return new Response(
      JSON.stringify({ success: false, error: String(err), priceStmts: priceStmts.length, fundStmts: fundStmts.length }),
      { status: 500, headers: { 'Content-Type': 'application/json' } }
    );
  }

  return new Response(
    JSON.stringify({ success: true, written: { prices: prices.length, fundamentals: fundamentals.length } }),
    { status: 200, headers: { 'Content-Type': 'application/json' } }
  );
}
