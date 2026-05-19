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
    parts.push(`=== 本期主文章 ===\n標題：${issueTitle}\n\n${mainContent}`);
  }
  links.forEach((lk, i) => {
    const articleBody = lk.content && lk.content.trim().length > 80
      ? lk.content
      : '[無法取得全文，請根據標題與你的知識補充重點]';
    const searchNote = lk.aiSearched ? '（內容來自 AI 聯網搜尋）' : '';
    parts.push(`=== 推薦閱讀 ${i + 1} ${searchNote}===\n標題：${lk.title}\n來源：${lk.url}\n\n${articleBody}`);
  });

  const totalLinks = links.length;

  const prompt = `你是資深半導體產業分析師。請將以下 Always Be Curious 週報（${issueNum} · ${issueDate}）整理成完整 PowerPoint 簡報。

本期共有 ${totalLinks} 篇推薦閱讀文章。

${parts.join('\n\n---\n\n')}

---

請直接輸出 JSON（不加任何說明或 markdown fence）：

{
  "title": "週報主標題（繁體中文，≤20字）",
  "subtitle": "${issueNum} · ${issueDate}",
  "slides": [
    {
      "id": 0,
      "type": "intro",
      "title": "本期 ${issueNum} 概覽（≤20字）",
      "subtitle": "${issueDate} · ${totalLinks} 篇推薦閱讀",
      "bullets": ["文章1一行核心主題", "文章2一行核心主題", ...全部${totalLinks}篇都要列],
      "source": ""
    },
    {
      "id": 1,
      "type": "main",
      "title": "主文章標題（精煉中文，≤25字）",
      "subtitle": "副標或來源（≤20字）",
      "bullets": ["核心洞察一（≤40字）", "核心洞察二", "核心洞察三", "核心洞察四"],
      "source": "https://..."
    },
    ... 每篇推薦閱讀一頁 (type: "link") ...
    {
      "id": ${totalLinks + 1},
      "type": "conclusion",
      "title": "本期總結與產業展望",
      "subtitle": "分析師觀點",
      "bullets": [
        "本期最重要趨勢1（具體說明）",
        "本期最重要趨勢2（具體說明）",
        "值得關注的機會或風險",
        "未來3個月預測",
        "建議追蹤的指標或事件"
      ],
      "source": ""
    }
  ]
}

嚴格規則：
- slides 陣列：第一個必須是 type:"intro"，最後一個必須是 type:"conclusion"
- 中間每篇推薦閱讀各佔一頁（type:"link"），主文章 type:"main"
- intro 的 bullets 要列出全部 ${totalLinks} 篇的一行主題摘要
- conclusion 是分析師真正的觀點、預測和建議，不是重複摘要
- 所有文字繁體中文，公司/人名保留英文（TSMC, Nvidia, Intel...）
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
