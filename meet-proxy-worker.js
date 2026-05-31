// meet-proxy — Cloudflare Worker
// Secrets needed: ANTHROPIC_KEY, BRAVE_KEY

const ALLOWED_ORIGIN = 'https://servanting.ca';
const CORS = {
  'Access-Control-Allow-Origin': ALLOWED_ORIGIN,
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type',
};

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }

    const path = new URL(request.url).pathname;
    const body = await request.json().catch(() => ({}));

    // ── /enrich ───────────────────────────────────────────────────────────────
    // Accepts: { name, email, meetingTitle, meetingDesc, emails[], lastMet, apolloCtx }
    // 1. Brave search for public info on the person
    // 2. Pass everything to Claude for structured enrichment
    if (path === '/enrich') {
      const { name, email, meetingTitle, meetingDesc, emails, lastMet } = body;

      // Step 1 — Brave search
      let webContext = '';
      if (env.BRAVE_KEY && name) {
        try {
          const query = encodeURIComponent(`${name} ${email.split('@')[1] || ''}`);
          const braveRes = await fetch(
            `https://api.search.brave.com/res/v1/web/search?q=${query}&count=3&text_decorations=false`,
            { headers: { 'Accept': 'application/json', 'X-Subscription-Token': env.BRAVE_KEY } }
          );
          const braveData = await braveRes.json();
          const results = (braveData.web && braveData.web.results) || [];
          webContext = results.slice(0, 3).map(r =>
            `${r.title}\n${r.description || ''}\n${r.url}`
          ).join('\n\n');
        } catch(e) {
          webContext = '';
        }
      }

      // Step 2 — Claude enrichment
      const emailCtx = (emails || []).slice(0, 3).map(e =>
        `Subject: ${e.subject}\nDate: ${e.date}\nSnippet: ${e.snippet}`
      ).join('\n---\n');

      const prompt = `Omar Yaqub (Executive Director, IslamicFamily Edmonton) is meeting ${name} (${email}).
Meeting: "${meetingTitle || ''}"
${meetingDesc ? 'Description: ' + meetingDesc.slice(0, 300) : ''}
${lastMet ? 'Last met: ' + lastMet : ''}

Web search results for this person:
${webContext || 'No results found.'}

Recent emails:
${emailCtx || 'None.'}

Reply ONLY with valid JSON, no markdown, no backticks:
{
  "title": "job title from web or email signature, else empty",
  "org": "organization name, else empty",
  "orgDesc": "one short phrase describing what their org does, else empty",
  "photo": "direct URL to a profile photo if found in web results, else empty",
  "linkedin": "full linkedin.com/in/... URL if found in web results, else empty",
  "emailSummary": "1-2 sentences of most relevant email context for this meeting, else empty",
  "emailDate": "most recent email date readable, else empty",
  "actionItems": "any specific open ask or commitment from emails, else empty",
  "meetingPurpose": "1 sentence on likely purpose of this meeting based on all context"
}`;

      const claudeRes = await fetch('https://api.anthropic.com/v1/messages', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-api-key': env.ANTHROPIC_KEY,
          'anthropic-version': '2023-06-01',
        },
        body: JSON.stringify({
          model: 'claude-sonnet-4-20250514',
          max_tokens: 600,
          messages: [{ role: 'user', content: prompt }],
        }),
      });

      const claudeData = await claudeRes.json();
      const txt = (claudeData.content && claudeData.content[0] && claudeData.content[0].text) || '{}';

      let parsed = {};
      try {
        parsed = JSON.parse(txt.replace(/```json|```/g, '').trim());
      } catch(e) {}

      return json({ ok: true, data: parsed });
    }

    return json({ error: 'not found' }, 404);
  }
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS, 'Content-Type': 'application/json' },
  });
}
