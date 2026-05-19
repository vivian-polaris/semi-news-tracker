export async function onRequestPost(context) {
  const apiKey = context.env.BYTEPLUS_API_KEY;
  const baseUrl = 'https://ark.ap-southeast.bytepluses.com/api/coding/v3';
  const cors = { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' };

  if (!apiKey) return new Response(JSON.stringify({ error: 'API key not configured' }), { status: 500, headers: cors });

  let body;
  try { body = await context.request.json(); }
  catch { return new Response(JSON.stringify({ error: 'Invalid JSON' }), { status: 400, headers: cors }); }

  const { issueTitle, issueNum, issueDate, mainContent, links = [] } = body;

  const parts = [];
  if (mainContent && mainContent.trim()) {
    parts.push(`=== 主文章 ===\n標題：${issueTitle}\n\n${mainContent}`);
  }
  links.forEach((lk, i) => {
    const body = lk.content && lk.content.trim().length > 100
      ? lk.content
      : '[無法取得全文，請根據標題與你的知識庫補充重點]';
    parts.push(`=== 推薦閱讀 ${i + 1} ===\n標題：${lk.title}\n來源：${lk.url}\n\n${body}`);
  });

  const prompt = `你是一位專業商業分析師，請將以下 Always Be Curious 週報整理成 PowerPoint 簡報結構。

週報：${issueNum} · ${issueDate}

${parts.join('\n\n---\n\n')}

---

請直接輸出 JSON，不要任何說明文字：
{
  "title": "週報主標題（中文，≤20字）",
  "subtitle": "${issueNum} · ${issueDate}",
  "slides": [
    {
      "id": 1,
      "title": "文章標題（精煉中文，≤25字）",
      "subtitle": "來源或副標（≤20字）",
      "bullets": ["重點一（≤35字）", "重點二", "重點三", "重點四"],
      "source": "https://...",
      "type": "main"
    }
  ]
}

規則：
- 每篇文章一頁 slide；主文 type: "main"，推薦閱讀 type: "link"
- bullets 3-5條，繁體中文，精煉有力，直接點出核心洞察
- 若某篇內容不足，根據標題與你的知識補充最相關重點
- 純 JSON，不加 markdown fencing`;

  let resp;
  try {
    resp = await fetch(`${baseUrl}/chat/completions`, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'glm-5.1',
        messages: [{ role: 'user', content: prompt }],
        thinking: { type: 'disabled' },
        max_tokens: 24000,
        temperature: 0.3,
        stream: false
      })
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: 'GLM network error: ' + e.message }), { status: 502, headers: cors });
  }

  if (!resp.ok) {
    const err = await resp.text().catch(() => '');
    return new Response(JSON.stringify({ error: `GLM ${resp.status}`, detail: err }), { status: resp.status, headers: cors });
  }

  const data = await resp.json();
  let content = data.choices?.[0]?.message?.content || '';
  const ti = content.indexOf('</think>');
  if (ti !== -1) content = content.slice(ti + 8).trim();
  // Strip markdown code fence if present
  content = content.replace(/^```[a-z]*\n?/i, '').replace(/\n?```$/i, '').trim();
  const jm = content.match(/\{[\s\S]*\}/);
  if (!jm) return new Response(JSON.stringify({ error: 'GLM non-JSON', raw: content.slice(0, 300) }), { status: 500, headers: cors });

  let slides;
  try { slides = JSON.parse(jm[0]); }
  catch { return new Response(JSON.stringify({ error: 'JSON parse failed', raw: content.slice(0, 300) }), { status: 500, headers: cors }); }

  return new Response(JSON.stringify(slides), { headers: cors });
}

export async function onRequestOptions() {
  return new Response(null, {
    headers: {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type'
    }
  });
}
