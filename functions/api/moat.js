export async function onRequestPost(context) {
  const apiKey = context.env.BYTEPLUS_API_KEY;
  const baseUrl = 'https://ark.ap-southeast.bytepluses.com/api/coding/v3';

  if (!apiKey) {
    return new Response(JSON.stringify({ error: 'API key not configured' }), {
      status: 500, headers: { 'Content-Type': 'application/json' }
    });
  }

  let body;
  try { body = await context.request.json(); } catch {
    return new Response(JSON.stringify({ error: 'Invalid JSON' }), {
      status: 400, headers: { 'Content-Type': 'application/json' }
    });
  }

  const { code, name, moatEntry } = body;
  if (!code || !name) {
    return new Response(JSON.stringify({ error: 'Missing code or name' }), {
      status: 400, headers: { 'Content-Type': 'application/json' }
    });
  }

  const sourcesStr = moatEntry?.moatSources?.join('、') || '未提供';
  const prompt = `你是台股護城河分析師。請用繁體中文，精煉有力，約200字，分析以下股票的護城河現況：

股票：${code} ${name}
護城河類型：${moatEntry?.moatType || '未知'}
護城河描述：${moatEntry?.moatDesc || '無'}
競爭優勢來源：${sourcesStr}
主要風險：${moatEntry?.risk || '未知'}

請依序分析以下三點，每點用・開頭，純文字，禁止 markdown：
・護城河是否依然有效？還是正在侵蝕？
・未來12個月最大威脅是什麼？
・對長線投資者的意義：是否值得等待股價回調後買入？`;

  const resp = await fetch(`${baseUrl}/chat/completions`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      model: 'glm-5.1',
      messages: [{ role: 'user', content: prompt }],
      thinking: { type: 'disabled' },
      max_tokens: 2048,
      temperature: 0.7,
      stream: false
    })
  });

  if (!resp.ok) {
    const err = await resp.text().catch(() => '');
    return new Response(JSON.stringify({ error: `GLM error ${resp.status}`, detail: err }), {
      status: resp.status, headers: { 'Content-Type': 'application/json' }
    });
  }

  const data = await resp.json();
  const content = data.choices?.[0]?.message?.content || '';
  return new Response(JSON.stringify({ analysis: content, code, name }), {
    headers: { 'Content-Type': 'application/json' }
  });
}
