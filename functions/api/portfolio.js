const H = { 'Content-Type': 'application/json' };

// POST /api/portfolio  { picks: [{code,name,price,score,vcpPivot,sector}] }
export async function onRequestPost(context) {
  const kv = context.env.VCP_KV;
  if (!kv) return new Response(JSON.stringify({ error: 'KV not configured' }), { status: 500, headers: H });

  let body;
  try { body = await context.request.json(); } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON' }), { status: 400, headers: H });
  }

  const { picks } = body;
  if (!picks || !Array.isArray(picks) || picks.length === 0) {
    return new Response(JSON.stringify({ error: 'Invalid picks' }), { status: 400, headers: H });
  }

  // Key = scan:YYYY-MM-DD, overwrite if same day
  const today = new Date().toISOString().slice(0, 10);
  await kv.put(`scan:${today}`, JSON.stringify({ date: today, picks }), {
    expirationTtl: 35 * 24 * 3600  // 35 days
  });

  return new Response(JSON.stringify({ ok: true, date: today, count: picks.length }), { headers: H });
}

// GET /api/portfolio  → { history: [{date, picks:[...]}] }
export async function onRequestGet(context) {
  const kv = context.env.VCP_KV;
  if (!kv) return new Response(JSON.stringify({ error: 'KV not configured' }), { status: 500, headers: H });

  const list = await kv.list({ prefix: 'scan:' });
  const days = await Promise.all(list.keys.map(k => kv.get(k.name, { type: 'json' })));
  const history = days.filter(Boolean).sort((a, b) => b.date.localeCompare(a.date));

  return new Response(JSON.stringify({ history }), { headers: H });
}
