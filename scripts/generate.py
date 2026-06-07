#!/usr/bin/env python3
"""
People & Purpose briefing generator.
Fetches calendar + email directly via Google APIs, then sends to Claude
for intelligence (bios, context, research). Renders to index.html.
"""

import os, json, re, requests
from datetime import datetime, timedelta
from dateutil import tz

# ── Config ────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
CALENDAR_ID          = "omary@islamicfamily.ca"
INTERNAL_DOMAINS     = {"islamicfamily.ca", "gmail.com"}  # omar.yaqub@gmail.com is internal
INTERNAL_EMAILS      = {"omar.yaqub@gmail.com", "omary@islamicfamily.ca"}
MOUNTAIN             = tz.gettz("America/Edmonton")
OFFICE_ADDRESS       = "10525 Jasper Ave"

# ── Google OAuth ──────────────────────────────────────────────────────────────

def get_access_token() -> str:
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": GOOGLE_REFRESH_TOKEN,
        "grant_type":    "refresh_token",
    }, timeout=15)
    r.raise_for_status()
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token: {r.text}")
    print("✓ Google access token obtained")
    return token

# ── Google Calendar ───────────────────────────────────────────────────────────

def fetch_events(token: str, date) -> list:
    start = datetime(date.year, date.month, date.day, 0, 0, 0, tzinfo=MOUNTAIN).isoformat()
    end   = datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=MOUNTAIN).isoformat()
    r = requests.get(
        f"https://www.googleapis.com/calendar/v3/calendars/{CALENDAR_ID}/events",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "timeMin": start, "timeMax": end,
            "singleEvents": "true", "orderBy": "startTime",
            "maxResults": 50,
        },
        timeout=20,
    )
    r.raise_for_status()
    events = r.json().get("items", [])
    print(f"  Calendar: {len(events)} events on {date}")
    return events

# ── Google Gmail ──────────────────────────────────────────────────────────────

def search_emails(token: str, query: str, max_results: int = 3) -> list[str]:
    """Return list of snippet strings matching the query."""
    r = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"q": query, "maxResults": max_results},
        timeout=15,
    )
    if not r.ok:
        return []
    threads = r.json().get("messages", [])
    snippets = []
    for t in threads[:max_results]:
        msg_r = requests.get(
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{t['id']}",
            headers={"Authorization": f"Bearer {token}"},
            params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
            timeout=10,
        )
        if msg_r.ok:
            payload = msg_r.json().get("payload", {})
            headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
            snippet = msg_r.json().get("snippet", "")
            snippets.append(f"[{headers.get('Date','')}] {headers.get('Subject','')} — {snippet}")
    return snippets

# ── Parse events ─────────────────────────────────────────────────────────────

def is_external(email: str) -> bool:
    if not email or email in INTERNAL_EMAILS:
        return False
    domain = email.split("@")[-1].lower()
    return domain not in INTERNAL_DOMAINS

def parse_events(events: list, token: str) -> dict:
    """Return counts and list of external meetings with attendee + email context."""
    internal_count = 0
    in_person_count = 0
    offsite_count = 0
    offsite_detail = ""
    external_meetings = []

    for ev in events:
        status = ev.get("status", "")
        if status == "cancelled":
            continue

        summary  = ev.get("summary", "Untitled")
        location = ev.get("location", "")
        start_dt = ev.get("start", {})
        time_str = ""
        dur_str  = ""

        # Parse time
        if "dateTime" in start_dt:
            dt = datetime.fromisoformat(start_dt["dateTime"])
            dt_mt = dt.astimezone(MOUNTAIN)
            time_str = dt_mt.strftime("%-I:%M %p")
            end_dt = ev.get("end", {})
            if "dateTime" in end_dt:
                end = datetime.fromisoformat(end_dt["dateTime"]).astimezone(MOUNTAIN)
                mins = int((end - dt_mt).total_seconds() / 60)
                dur_str = f"{mins} min" if mins < 60 else f"{mins//60}h{' '+str(mins%60)+'m' if mins%60 else ''}"
        elif "date" in start_dt:
            time_str = "All day"

        # Location type
        meet_link = ""
        meet_platform = ""
        meet_emoji = "📹"
        conf_data = ev.get("conferenceData", {})
        for ep in conf_data.get("entryPoints", []):
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri", "")
                label = ep.get("label", "").lower()
                if "meet" in label or "meet.google" in meet_link:
                    meet_platform = "Google Meet"
                    meet_emoji = "📹"
                elif "zoom" in label or "zoom" in meet_link:
                    meet_platform = "Zoom"
                    meet_emoji = "📹"
                elif "teams" in label or "teams" in meet_link:
                    meet_platform = "Microsoft Teams"
                    meet_emoji = "💻"
                else:
                    meet_platform = "Video call"
                break
        # Fallback: check description for links
        if not meet_link:
            desc = ev.get("description", "") or ""
            for pattern, platform, emoji in [
                (r"https://meet\.google\.com/\S+", "Google Meet", "📹"),
                (r"https://[^\s]*zoom\.us/\S+",   "Zoom",        "📹"),
                (r"https://teams\.microsoft\.com/\S+", "Microsoft Teams", "💻"),
            ]:
                m = re.search(pattern, desc)
                if m:
                    meet_link = m.group(0).rstrip(".,)")
                    meet_platform = platform
                    meet_emoji = emoji
                    break

        is_virtual   = bool(meet_link)
        is_in_person = bool(location) and not is_virtual
        is_offsite   = is_in_person and OFFICE_ADDRESS not in location

        # Attendees
        attendees = ev.get("attendees", [])
        external_attendees = [
            a for a in attendees
            if is_external(a.get("email", "")) and a.get("responseStatus") != "declined"
        ]

        # Count internal vs external
        if not external_attendees:
            internal_count += 1
        if is_in_person:
            in_person_count += 1
        if is_offsite:
            offsite_count += 1
            if not offsite_detail:
                offsite_detail = f"{summary} · {location}"

        if external_attendees:
            # Fetch email context for each external person
            people_data = []
            for att in external_attendees:
                email = att.get("email", "")
                name  = att.get("displayName") or email.split("@")[0]
                snippets = search_emails(token, f"from:{email} OR to:{email}", max_results=2)
                people_data.append({
                    "name":    name,
                    "email":   email,
                    "email_snippets": snippets,
                })

            external_meetings.append({
                "time":          time_str,
                "duration":      dur_str,
                "title":         summary,
                "location":      location,
                "meet_link":     meet_link,
                "meet_platform": meet_platform,
                "meet_emoji":    meet_emoji,
                "people":        people_data,
            })

    return {
        "internal_count":  internal_count,
        "in_person_count": in_person_count,
        "offsite_count":   offsite_count,
        "offsite_detail":  offsite_detail,
        "meetings":        external_meetings,
    }

# ── Weather ───────────────────────────────────────────────────────────────────

def fetch_weather() -> dict:
    """Open-Meteo — no API key required."""
    try:
        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": 53.5461, "longitude": -113.4938,
                "daily": "temperature_2m_max,weathercode",
                "timezone": "America/Edmonton",
                "forecast_days": 3,
            },
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()["daily"]
        def wcode_to_emoji(code):
            if code == 0:            return "☀️"
            elif code <= 3:          return "🌤️"
            elif code <= 48:         return "🌫️"
            elif code <= 67:         return "🌧️"
            elif code <= 77:         return "🌨️"
            elif code <= 82:         return "🌦️"
            else:                    return "⛈️"
        def wcode_to_text(code):
            if code == 0:            return "Clear"
            elif code <= 3:          return "Partly cloudy"
            elif code <= 48:         return "Foggy"
            elif code <= 67:         return "Rain"
            elif code <= 77:         return "Snow"
            elif code <= 82:         return "Showers"
            else:                    return "Thunderstorm"
        temps  = data["temperature_2m_max"]
        codes  = data["weathercode"]
        return {
            "day0_temp": round(temps[0]), "day0_icon": wcode_to_emoji(codes[0]), "day0_cond": wcode_to_text(codes[0]),
            "day1_temp": round(temps[1]), "day1_icon": wcode_to_emoji(codes[1]), "day1_cond": wcode_to_text(codes[1]),
        }
    except Exception as e:
        print(f"  Weather fetch failed: {e}")
        return {"day0_temp":"—","day0_icon":"🌤️","day0_cond":"","day1_temp":"—","day1_icon":"🌤️","day1_cond":""}

# ── Claude for intelligence ───────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a personal briefing assistant for Omar Yaqub, Executive Director of IslamicFamily in Edmonton, Alberta (nonprofit serving newcomer, refugee, and Muslim communities for 35+ years).

You will receive structured calendar and email data for two days. For each external meeting attendee, you must:
1. Research who they are using web search — their role, organisation, background
2. Find a public photo URL (university/org profile page, conference speaker page, LinkedIn photo — must be a direct image URL ending in jpg/png/webp)
3. Find their LinkedIn URL
4. Write 2-3 sentences on who they are and why this relationship matters to Omar's work (IslamicFamily, halal housing, social finance, nonprofit sector, community development)
5. Summarise any email context provided into 1-2 sentences about this specific meeting

Return ONLY valid JSON, no markdown fences, in this exact shape:

{
  "days": [
    {
      "date": "2026-06-08",
      "label": "Monday, June 8",
      "tab_label": "Mon Jun 8",
      "summary": "One sentence describing the external meetings for this day.",
      "stats": {
        "external": 2,
        "internal": 4,
        "in_person": 1,
        "offsite": 0,
        "offsite_detail": ""
      },
      "weather": {
        "icon": "☀️",
        "temp_c": 18,
        "condition": "Sunny"
      },
      "meetings": [
        {
          "time": "9:00 AM",
          "duration": "30 min",
          "title": "Calendar title",
          "meet_link": "https://... or null",
          "meet_platform": "Google Meet",
          "meet_emoji": "📹",
          "topic_tag": "Short topic label",
          "same_call_as": null,
          "people": [
            {
              "name": "Full Name",
              "role": "Title · Organisation",
              "initials": "FQ",
              "photo_url": "https://direct-image-url.jpg or null",
              "linkedin_url": "https://linkedin.com/in/... or null",
              "who_they_are": "2-3 sentences.",
              "email_context": "1-2 sentence summary or null"
            }
          ]
        }
      ]
    }
  ]
}"""


def call_claude(day_data: list, weather: dict) -> dict:
    # Trim email snippets to keep prompt small
    trimmed = []
    for day in day_data:
        day_copy = dict(day)
        meetings_copy = []
        for m in day.get("meetings", []):
            m_copy = dict(m)
            people_copy = []
            for p in m.get("people", []):
                p_copy = dict(p)
                # Keep only first snippet, truncated
                snippets = p_copy.get("email_snippets", [])
                p_copy["email_snippets"] = [s[:200] for s in snippets[:1]]
                people_copy.append(p_copy)
            m_copy["people"] = people_copy
            meetings_copy.append(m_copy)
        day_copy["meetings"] = meetings_copy
        trimmed.append(day_copy)

    user_message = f"""Here is the raw calendar and email data for two days. Research each external person and return the structured JSON.

Weather:
- Day 1: {weather['day0_icon']} {weather['day0_temp']}°C {weather['day0_cond']}
- Day 2: {weather['day1_icon']} {weather['day1_temp']}°C {weather['day1_cond']}

Data:
{json.dumps(trimmed, indent=2)}"""

    payload = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_message}],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    print("Calling Claude API…")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=payload,
        timeout=180,
    )
    if not r.ok:
        print("API error:", r.text)
    r.raise_for_status()

    data = r.json()
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "\n".join(text_blocks).strip()
    print(f"  Claude response length: {len(raw)} chars")
    print(f"  Stop reason: {data.get('stop_reason')}")
    if len(raw) < 500:
        print(f"  Raw response: {raw!r}")
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    if not raw:
        raise ValueError(f"Empty response from Claude. Stop reason: {data.get('stop_reason')}")
    return json.loads(raw)

# ── HTML rendering ────────────────────────────────────────────────────────────

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>People &amp; Purpose · {{PAGE_TITLE}}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,400;0,500;1,400&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,400&display=swap" rel="stylesheet">
<style>
:root {
  --ink:#1c1c1a;--ink-muted:#6b6b62;--ink-faint:#a8a79e;
  --paper:#f8f7f2;--paper-warm:#f1efe8;--card:#ffffff;
  --forest:#2b4a2e;--forest-pale:#e6ede7;--forest-mid:#4a7c4f;
  --gold:#b5781e;--gold-pale:#fdf4e5;
  --border:rgba(28,28,26,0.08);--border-mid:rgba(28,28,26,0.14);--r:13px;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'DM Sans',sans-serif;background:var(--paper);color:var(--ink);min-height:100vh;-webkit-font-smoothing:antialiased;}
.tabs{background:var(--paper-warm);border-bottom:1px solid var(--border-mid);display:flex;}
.tab{flex:1;padding:.9rem 1.5rem;font-size:13px;font-weight:500;color:var(--ink-muted);border:none;background:transparent;cursor:pointer;border-bottom:2.5px solid transparent;transition:all .13s;font-family:'DM Sans',sans-serif;text-align:center;}
.tab.on{color:var(--forest);border-bottom-color:var(--forest);background:var(--paper);}
.tab:hover:not(.on){background:rgba(0,0,0,.025);}
.main{max-width:940px;margin:0 auto;padding:2.5rem 2rem 5rem;}
.day{display:none;}.day.on{display:block;}
.chip{background:var(--forest-pale);border:1px solid rgba(43,74,46,.13);border-radius:10px;padding:1rem 1.25rem;color:var(--forest);margin-bottom:2rem;line-height:1.5;}
.chip-summary{font-size:13.5px;margin-bottom:.85rem;}
.chip-stats{display:flex;flex-wrap:wrap;gap:.5rem;align-items:center;}
.stat{display:inline-flex;align-items:center;gap:5px;background:rgba(43,74,46,.08);border:1px solid rgba(43,74,46,.12);border-radius:7px;padding:4px 10px;font-size:12px;color:var(--forest);white-space:nowrap;}
.stat strong{font-weight:600;}.stat-icon{font-size:12px;line-height:1;}
.offsite-sub{font-size:11px;opacity:.7;margin-left:2px;}
.stat-divider{width:1px;height:16px;background:rgba(43,74,46,.15);margin:0 1px;}
.weather-stat{display:inline-flex;align-items:center;gap:6px;background:rgba(43,74,46,.08);border:1px solid rgba(43,74,46,.12);border-radius:7px;padding:4px 10px;font-size:12px;color:var(--forest);white-space:nowrap;}
.weather-cond{opacity:.65;font-size:11.5px;}
.slabel{font-size:10px;font-weight:500;letter-spacing:.12em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:1.25rem;padding-bottom:.55rem;border-bottom:1px solid var(--border);}
.cards{display:flex;flex-direction:column;gap:1.5rem;margin-bottom:3rem;}
.mcard{background:var(--card);border:1px solid var(--border);border-radius:var(--r);overflow:hidden;transition:box-shadow .18s;}
.mcard:hover{box-shadow:0 3px 10px rgba(0,0,0,.07),0 10px 30px rgba(0,0,0,.05);}
.mcard-grid{display:grid;grid-template-columns:104px 1fr;}
.pcol{background:var(--paper-warm);display:flex;flex-direction:column;align-items:center;padding:1.6rem .7rem 1.2rem;border-right:1px solid var(--border);gap:.65rem;}
.avatar{width:58px;height:58px;border-radius:50%;object-fit:cover;object-position:top center;border:2.5px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.14);display:block;}
.avatar-init{width:58px;height:58px;border-radius:50%;background:var(--forest-pale);color:var(--forest);display:flex;align-items:center;justify-content:center;font-size:16px;font-weight:500;border:2.5px solid #fff;box-shadow:0 2px 8px rgba(0,0,0,.12);}
.time-box{text-align:center;line-height:1.4;}
.time-main{font-size:11.5px;font-weight:500;color:var(--ink-muted);}
.time-dur{font-size:10px;color:var(--ink-faint);}
.mbody{padding:1.4rem 1.65rem;}
.pname{font-family:'Playfair Display',serif;font-size:1.3rem;color:var(--ink);line-height:1.15;}
.prole{font-size:12.5px;color:var(--ink-muted);margin-top:3px;line-height:1.4;}
.pill{font-size:11.5px;padding:3px 10px;border-radius:100px;display:inline-flex;align-items:center;gap:4px;line-height:1.4;}
.pill-t{background:var(--paper-warm);color:var(--ink-muted);border:1px solid var(--border);}
.blk{margin-bottom:.9rem;}
.blk-lbl{font-size:10px;font-weight:500;letter-spacing:.09em;text-transform:uppercase;color:var(--ink-faint);margin-bottom:.3rem;}
.blk-txt{font-size:13.5px;color:var(--ink);line-height:1.65;}
.blk-txt.quote{background:var(--paper-warm);border-left:3px solid var(--forest-mid);padding:.55rem 1rem;border-radius:0 7px 7px 0;font-size:13px;color:var(--ink-muted);font-style:italic;}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin:.6rem 0 .9rem;}
.btn{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:100px;font-size:11.5px;font-weight:500;text-decoration:none;white-space:nowrap;font-family:'DM Sans',sans-serif;line-height:1.4;transition:opacity .13s;}
.btn:hover{opacity:.78;}
.btn-join{background:var(--forest-pale);color:var(--forest);border:1px solid rgba(43,74,46,.18);}
.btn-li{background:var(--gold-pale);color:var(--gold);border:1px solid rgba(181,120,30,.18);}
.mfooter{margin-top:.9rem;padding-top:.9rem;border-top:1px solid var(--border);}
.same-call-badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;color:var(--ink-faint);background:var(--paper-warm);border:1px solid var(--border);border-radius:100px;padding:2px 9px;margin-bottom:.5rem;}
.empty{background:var(--card);border:1px dashed var(--border-mid);border-radius:var(--r);padding:3rem 2rem;text-align:center;color:var(--ink-muted);}
.empty-title{font-family:'Playfair Display',serif;font-size:1.15rem;margin-bottom:.4rem;color:var(--ink);}
.stamp{text-align:center;font-size:11px;color:var(--ink-faint);margin-top:2.5rem;padding-top:1.25rem;border-top:1px solid var(--border);line-height:1.7;}
@media(max-width:700px){
  .main{padding:1.5rem 1rem 3rem;}
  .mcard-grid{grid-template-columns:80px 1fr;}
  .mbody{padding:1rem 1.1rem;}
  .pcol{padding:1.25rem .5rem 1rem;}
  .avatar,.avatar-init{width:46px;height:46px;font-size:13px;}
}
</style>
</head>
<body>
<nav class="tabs">{{TABS}}</nav>
<main class="main">
{{SECTIONS}}
<p class="stamp">Generated {{GENERATED_AT}} · Mountain Time</p>
</main>
<script>
function sw(which){
  document.querySelectorAll('.day').forEach(d=>d.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  document.getElementById('d-'+which).classList.add('on');
  document.getElementById('t-'+which).classList.add('on');
}
</script>
</body>
</html>"""


def h(t):
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")

def render_person(meeting, person, is_same_call=False):
    name       = h(person.get("name",""))
    role       = h(person.get("role",""))
    initials   = h(person.get("initials","?"))
    photo_url  = person.get("photo_url") or ""
    linkedin   = person.get("linkedin_url") or ""
    who        = h(person.get("who_they_are",""))
    email_ctx  = person.get("email_context")
    same_as    = person.get("same_call_as") or meeting.get("same_call_as")
    meet_link  = h(meeting.get("meet_link") or "")
    platform   = h(meeting.get("meet_platform",""))
    emoji      = meeting.get("meet_emoji","📹")
    topic      = h(meeting.get("topic_tag",""))
    time_main  = h(meeting.get("time",""))
    dur        = h(meeting.get("duration","") if not is_same_call else "Same call")

    avatar = (f'<img class="avatar" src="{h(photo_url)}" alt="{name}" '
              f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
              f'<div class="avatar-init" style="display:none">{initials}</div>'
              if photo_url else f'<div class="avatar-init">{initials}</div>')

    same_badge = f'<span class="same-call-badge">↑ Same call as {h(same_as)}</span>\n          ' if same_as else ""
    join_btn   = f'<a class="btn btn-join" href="{meet_link}" target="_blank">{emoji} {platform} ↗</a>\n            ' if meet_link else ""
    li_btn     = f'<a class="btn btn-li" href="{h(linkedin)}" target="_blank">LinkedIn ↗</a>\n            ' if linkedin else ""
    topic_pill = f'<span class="pill pill-t">{topic}</span>' if topic else ""
    email_blk  = (f'<div class="blk"><div class="blk-lbl">From your emails</div>'
                  f'<div class="blk-txt quote">{h(email_ctx)}</div></div>') if email_ctx else ""

    return f"""
    <article class="mcard">
      <div class="mcard-grid">
        <div class="pcol">
          {avatar}
          <div class="time-box"><div class="time-main">{time_main}</div><div class="time-dur">{dur}</div></div>
        </div>
        <div class="mbody">
          {same_badge}<div class="pname">{name}</div>
          <div class="prole">{role}</div>
          <div class="btns">{join_btn}{li_btn}{topic_pill}</div>
          {email_blk}
          <div class="mfooter">
            <div class="blk-lbl">Who they are</div>
            <div class="blk-txt" style="font-size:13px">{who}</div>
          </div>
        </div>
      </div>
    </article>"""

def render_section(day, idx, is_first):
    day_id   = f"day{idx}"
    on_cls   = " on" if is_first else ""
    label    = h(day.get("label",""))
    summary  = h(day.get("summary",""))
    stats    = day.get("stats", {})
    weather  = day.get("weather", {})
    meetings = day.get("meetings", [])

    ext   = stats.get("external", 0)
    inte  = stats.get("internal", 0)
    ip    = stats.get("in_person", 0)
    off   = stats.get("offsite", 0)
    offd  = h(stats.get("offsite_detail",""))
    wi    = weather.get("icon","🌤️")
    wt    = weather.get("temp_c","—")
    wc    = h(weather.get("condition",""))
    offsite_sub = f' <span class="offsite-sub">· {offd}</span>' if offd else ""

    stats_html = f"""
      <div class="stat"><span class="stat-icon">🤝</span><span><strong>{ext}</strong> external</span></div>
      <div class="stat"><span class="stat-icon">👥</span><span><strong>{inte}</strong> internal</span></div>
      <div class="stat"><span class="stat-icon">📍</span><span><strong>{ip}</strong> in-person</span></div>
      <div class="stat"><span class="stat-icon">🚗</span><span><strong>{off}</strong> offsite{offsite_sub}</span></div>
      <div class="stat-divider"></div>
      <div class="weather-stat"><span>{wi}</span><strong>{wt}°C</strong><span class="weather-cond">{wc}</span></div>"""

    if meetings:
        cards = "\n".join(
            render_person(m, p, pi > 0)
            for m in meetings
            for pi, p in enumerate(m.get("people", []))
        )
        body = f'<p class="slabel">External meetings · {label}</p><div class="cards">{cards}\n  </div>'
    else:
        body = f'<div class="empty"><div class="empty-title">No external meetings.</div><p>No outside attendees on {label}.</p></div>'

    return f"""
<section class="day{on_cls}" id="d-{day_id}">
  <div class="chip">
    <div class="chip-summary">{summary}</div>
    <div class="chip-stats">{stats_html}
    </div>
  </div>
  {body}
</section>"""

def render_html(data, generated_at):
    days = data.get("days", [])
    tabs = "\n".join(
        f'  <button class="tab{" on" if i==0 else ""}" onclick="sw(\'day{i}\')" id="t-day{i}">'
        f'{h(d.get("tab_label", d.get("label","")))}</button>'
        for i, d in enumerate(days)
    )
    sections   = "\n".join(render_section(d, i, i==0) for i, d in enumerate(days))
    page_title = " & ".join(d.get("tab_label","") for d in days[:2])
    return (HTML_TEMPLATE
            .replace("{{PAGE_TITLE}}", h(page_title))
            .replace("{{TABS}}", tabs)
            .replace("{{SECTIONS}}", sections)
            .replace("{{GENERATED_AT}}", h(generated_at)))

# ── Main ──────────────────────────────────────────────────────────────────────

def get_target_dates():
    raw = os.environ.get("TARGET_DATE","").strip()
    if raw:
        from datetime import date
        base = datetime.strptime(raw, "%Y-%m-%d").date()
    else:
        base = datetime.now(MOUNTAIN).date() + timedelta(days=1)
    return base, base + timedelta(days=1)

def date_label(d):
    return d.strftime("%A, %B %-d")

def main():
    day0, day1 = get_target_dates()
    print(f"Generating briefing for {date_label(day0)} and {date_label(day1)}…")

    token   = get_access_token()
    weather = fetch_weather()

    day_data = []
    for d in [day0, day1]:
        events  = fetch_events(token, d)
        parsed  = parse_events(events, token)
        day_data.append({
            "date":          d.isoformat(),
            "label":         date_label(d),
            "internal":      parsed["internal_count"],
            "in_person":     parsed["in_person_count"],
            "offsite":       parsed["offsite_count"],
            "offsite_detail":parsed["offsite_detail"],
            "meetings":      parsed["meetings"],
        })

    print(f"  Day 0: {len(day_data[0]['meetings'])} external meeting(s)")
    print(f"  Day 1: {len(day_data[1]['meetings'])} external meeting(s)")

    result = call_claude(day_data, weather)

    now_mt       = datetime.now(MOUNTAIN)
    generated_at = now_mt.strftime("%-I:%M %p, %A %B %-d %Y")
    html         = render_html(result, generated_at)

    out = os.path.join(os.path.dirname(__file__), "..", "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"✓ Written index.html ({len(html):,} bytes)")

if __name__ == "__main__":
    main()
