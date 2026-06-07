#!/usr/bin/env python3
"""
People & Purpose briefing generator.
Runs at 5pm MT the day before. Calls Claude with Google Calendar + Gmail MCPs
to fetch real data, then renders it into index.html.
"""

import os
import json
import sys
import requests
from datetime import datetime, timedelta, timezone
from dateutil import tz

# ── Config ────────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
GOOGLE_REFRESH_TOKEN = os.environ["GOOGLE_REFRESH_TOKEN"]
GOOGLE_CLIENT_ID     = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
CALENDAR_ID          = "omary@islamicfamily.ca"

MOUNTAIN = tz.gettz("America/Edmonton")

# ── Date targeting ────────────────────────────────────────────────────────────

def get_target_dates():
    """Return (today, tomorrow) as date strings in Mountain Time."""
    raw = os.environ.get("TARGET_DATE", "").strip()
    if raw:
        base = datetime.strptime(raw, "%Y-%m-%d").date()
    else:
        base = datetime.now(MOUNTAIN).date() + timedelta(days=1)  # "tomorrow"

    today    = base
    tomorrow = base + timedelta(days=1)
    return today, tomorrow


def date_label(d):
    return d.strftime("%A, %B %-d")


def iso_range(d):
    start = datetime(d.year, d.month, d.day,  0,  0,  0, tzinfo=MOUNTAIN).isoformat()
    end   = datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=MOUNTAIN).isoformat()
    return start, end

# ── Claude API call ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a personal briefing assistant for Omar Yaqub, Executive Director of IslamicFamily in Edmonton, Alberta.

Your job is to generate a People & Purpose daily briefing by reading Omar's Google Calendar and Gmail, then returning structured JSON.

INSTRUCTIONS:
1. Use the Google Calendar MCP to list events for the dates provided. Calendar ID: omary@islamicfamily.ca
2. Identify EXTERNAL attendees in each event (not @islamicfamily.ca or omar.yaqub@gmail.com addresses).
3. For each external meeting, search Gmail for recent email threads with those attendees to understand context.
4. Use web search to find each external person's public bio, role, photo URL, and LinkedIn.
5. Also count: total internal meetings, in-person meetings (have a physical location), offsite meetings (location that is NOT the IslamicFamily office at 10525 Jasper Ave).
6. Fetch Edmonton weather for both days.

Return ONLY valid JSON — no markdown fences, no preamble — in this exact shape:

{
  "days": [
    {
      "date": "2026-06-06",
      "label": "Friday, June 6",
      "tab_label": "Fri Jun 6",
      "summary": "One sentence describing the external meetings.",
      "stats": {
        "external": 3,
        "internal": 4,
        "in_person": 1,
        "offsite": 1,
        "offsite_detail": "Jummah · Location Name"
      },
      "weather": {
        "icon": "☀️",
        "temp_c": 17,
        "condition": "Sunny",
        "next_day_temp_c": 19
      },
      "meetings": [
        {
          "time": "9:00 AM",
          "duration": "30 min",
          "title": "Calendar event title",
          "meet_link": "https://... or null",
          "meet_platform": "Google Meet",
          "meet_emoji": "📹",
          "topic_tag": "Short topic label",
          "same_call_as": "Person Name or null",
          "people": [
            {
              "name": "Full Name",
              "role": "Title · Organisation",
              "initials": "FQ",
              "photo_url": "https://... or null",
              "linkedin_url": "https://... or null",
              "who_they_are": "2-3 sentences. Who they are and why relevant to IslamicFamily, halal housing, social finance, or community development.",
              "email_context": "1-2 sentences from recent email thread about this meeting, or null."
            }
          ]
        }
      ]
    }
  ]
}

Omit days with zero external meetings from the meetings array (keep the day entry, just leave meetings as []).
Return ONLY the JSON object."""


def get_google_access_token() -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "refresh_token": GOOGLE_REFRESH_TOKEN,
            "grant_type":    "refresh_token",
        },
        timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in Google response: {resp.text}")
    print("✓ Google access token obtained")
    return token


def call_claude(today: str, tomorrow: str, today_label: str, tomorrow_label: str) -> dict:
    access_token = get_google_access_token()

    user_message = f"""Generate a People & Purpose briefing for these two days:

Day 1: {today_label} ({today})
Day 2: {tomorrow_label} ({tomorrow})

Fetch calendar events for both days from omary@islamicfamily.ca, find external attendees,
pull email context, research each person, and return the structured JSON."""

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 4000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_message}],
        "mcp_servers": [
            {
                "type": "url",
                "url": "https://calendarmcp.googleapis.com/mcp/v1",
                "name": "google-calendar",
                "authentication": {
                    "type": "bearer",
                    "token": access_token,
                },
            },
            {
                "type": "url",
                "url": "https://gmailmcp.googleapis.com/mcp/v1",
                "name": "gmail",
                "authentication": {
                    "type": "bearer",
                    "token": access_token,
                },
            },
        ],
        "tools": [{"type": "web_search_20250305", "name": "web_search"}],
    }

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "mcp-client-2025-04-04",
        "content-type": "application/json",
    }

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=headers,
        json=payload,
        timeout=120,
    )
    if not resp.ok:
        print("API error response:", resp.text)
    resp.raise_for_status()
    data = resp.json()

    # Extract the JSON text from Claude's response
    text_blocks = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    raw = "\n".join(text_blocks)

    # Strip any accidental markdown fences
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

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
  --ink: #1c1c1a; --ink-muted: #6b6b62; --ink-faint: #a8a79e;
  --paper: #f8f7f2; --paper-warm: #f1efe8; --card: #ffffff;
  --forest: #2b4a2e; --forest-pale: #e6ede7; --forest-mid: #4a7c4f;
  --gold: #b5781e; --gold-pale: #fdf4e5;
  --border: rgba(28,28,26,0.08); --border-mid: rgba(28,28,26,0.14); --r: 13px;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'DM Sans', sans-serif; background: var(--paper); color: var(--ink); min-height: 100vh; -webkit-font-smoothing: antialiased; }
.tabs { background: var(--paper-warm); border-bottom: 1px solid var(--border-mid); display: flex; }
.tab { flex: 1; padding: 0.9rem 1.5rem; font-size: 13px; font-weight: 500; color: var(--ink-muted); border: none; background: transparent; cursor: pointer; border-bottom: 2.5px solid transparent; transition: all 0.13s; font-family: 'DM Sans', sans-serif; text-align: center; }
.tab.on { color: var(--forest); border-bottom-color: var(--forest); background: var(--paper); }
.tab:hover:not(.on) { background: rgba(0,0,0,0.025); }
.main { max-width: 940px; margin: 0 auto; padding: 2.5rem 2rem 5rem; }
.day { display: none; } .day.on { display: block; }
.chip { background: var(--forest-pale); border: 1px solid rgba(43,74,46,0.13); border-radius: 10px; padding: 1rem 1.25rem; color: var(--forest); margin-bottom: 2rem; line-height: 1.5; }
.chip-summary { font-size: 13.5px; margin-bottom: 0.85rem; }
.chip-stats { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
.stat { display: inline-flex; align-items: center; gap: 5px; background: rgba(43,74,46,0.08); border: 1px solid rgba(43,74,46,0.12); border-radius: 7px; padding: 4px 10px; font-size: 12px; color: var(--forest); white-space: nowrap; }
.stat strong { font-weight: 600; } .stat-icon { font-size: 12px; line-height: 1; }
.offsite-sub { font-size: 11px; opacity: 0.7; margin-left: 2px; }
.stat-divider { width: 1px; height: 16px; background: rgba(43,74,46,0.15); margin: 0 1px; }
.weather-stat { display: inline-flex; align-items: center; gap: 6px; background: rgba(43,74,46,0.08); border: 1px solid rgba(43,74,46,0.12); border-radius: 7px; padding: 4px 10px; font-size: 12px; color: var(--forest); white-space: nowrap; }
.weather-cond { opacity: 0.65; font-size: 11.5px; }
.slabel { font-size: 10px; font-weight: 500; letter-spacing: 0.12em; text-transform: uppercase; color: var(--ink-faint); margin-bottom: 1.25rem; padding-bottom: 0.55rem; border-bottom: 1px solid var(--border); }
.cards { display: flex; flex-direction: column; gap: 1.5rem; margin-bottom: 3rem; }
.mcard { background: var(--card); border: 1px solid var(--border); border-radius: var(--r); overflow: hidden; transition: box-shadow 0.18s; }
.mcard:hover { box-shadow: 0 3px 10px rgba(0,0,0,0.07), 0 10px 30px rgba(0,0,0,0.05); }
.mcard-grid { display: grid; grid-template-columns: 104px 1fr; }
.pcol { background: var(--paper-warm); display: flex; flex-direction: column; align-items: center; padding: 1.6rem 0.7rem 1.2rem; border-right: 1px solid var(--border); gap: 0.65rem; }
.avatar { width: 58px; height: 58px; border-radius: 50%; object-fit: cover; object-position: top center; border: 2.5px solid #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.14); display: block; }
.avatar-init { width: 58px; height: 58px; border-radius: 50%; background: var(--forest-pale); color: var(--forest); display: flex; align-items: center; justify-content: center; font-size: 16px; font-weight: 500; border: 2.5px solid #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.12); }
.time-box { text-align: center; line-height: 1.4; }
.time-main { font-size: 11.5px; font-weight: 500; color: var(--ink-muted); }
.time-dur { font-size: 10px; color: var(--ink-faint); }
.mbody { padding: 1.4rem 1.65rem; }
.pname { font-family: 'Playfair Display', serif; font-size: 1.3rem; color: var(--ink); line-height: 1.15; }
.prole { font-size: 12.5px; color: var(--ink-muted); margin-top: 3px; line-height: 1.4; }
.pill { font-size: 11.5px; padding: 3px 10px; border-radius: 100px; display: inline-flex; align-items: center; gap: 4px; line-height: 1.4; }
.pill-t { background: var(--paper-warm); color: var(--ink-muted); border: 1px solid var(--border); }
.blk { margin-bottom: 0.9rem; }
.blk-lbl { font-size: 10px; font-weight: 500; letter-spacing: 0.09em; text-transform: uppercase; color: var(--ink-faint); margin-bottom: 0.3rem; }
.blk-txt { font-size: 13.5px; color: var(--ink); line-height: 1.65; }
.blk-txt.quote { background: var(--paper-warm); border-left: 3px solid var(--forest-mid); padding: 0.55rem 1rem; border-radius: 0 7px 7px 0; font-size: 13px; color: var(--ink-muted); font-style: italic; }
.btns { display: flex; flex-wrap: wrap; gap: 6px; margin: 0.6rem 0 0.9rem; }
.btn { display: inline-flex; align-items: center; gap: 4px; padding: 3px 10px; border-radius: 100px; font-size: 11.5px; font-weight: 500; text-decoration: none; white-space: nowrap; font-family: 'DM Sans', sans-serif; line-height: 1.4; transition: opacity 0.13s; }
.btn:hover { opacity: 0.78; }
.btn-join { background: var(--forest-pale); color: var(--forest); border: 1px solid rgba(43,74,46,0.18); }
.btn-li { background: var(--gold-pale); color: var(--gold); border: 1px solid rgba(181,120,30,0.18); }
.mfooter { margin-top: 0.9rem; padding-top: 0.9rem; border-top: 1px solid var(--border); }
.same-call-badge { display: inline-flex; align-items: center; gap: 4px; font-size: 11px; color: var(--ink-faint); background: var(--paper-warm); border: 1px solid var(--border); border-radius: 100px; padding: 2px 9px; margin-bottom: 0.5rem; }
.empty { background: var(--card); border: 1px dashed var(--border-mid); border-radius: var(--r); padding: 3rem 2rem; text-align: center; color: var(--ink-muted); }
.empty-title { font-family: 'Playfair Display', serif; font-size: 1.15rem; margin-bottom: 0.4rem; color: var(--ink); }
.stamp { text-align: center; font-size: 11px; color: var(--ink-faint); margin-top: 2.5rem; padding-top: 1.25rem; border-top: 1px solid var(--border); line-height: 1.7; }
@media (max-width: 700px) {
  .main { padding: 1.5rem 1rem 3rem; }
  .mcard-grid { grid-template-columns: 80px 1fr; }
  .mbody { padding: 1rem 1.1rem; }
  .pcol { padding: 1.25rem 0.5rem 1rem; }
  .avatar, .avatar-init { width: 46px; height: 46px; font-size: 13px; }
}
</style>
</head>
<body>

<nav class="tabs">
{{TABS}}
</nav>

<main class="main">
{{SECTIONS}}
<p class="stamp">Generated {{GENERATED_AT}} · Mountain Time</p>
</main>

<script>
function sw(which) {
  document.querySelectorAll('.day').forEach(d => d.classList.remove('on'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
  document.getElementById('d-' + which).classList.add('on');
  document.getElementById('t-' + which).classList.add('on');
}
</script>
</body>
</html>"""


def h(text: str) -> str:
    """Minimal HTML escaping."""
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def render_person_card(meeting: dict, person: dict, day_idx: int) -> str:
    initials  = h(person.get("initials", "?"))
    photo_url = person.get("photo_url") or ""
    name      = h(person.get("name", ""))
    role      = h(person.get("role", ""))
    who       = person.get("who_they_are", "")
    email_ctx = person.get("email_context")
    linkedin  = person.get("linkedin_url") or ""

    # Avatar
    if photo_url:
        avatar_html = (
            f'<img class="avatar" src="{h(photo_url)}" alt="{name}" '
            f'onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">'
            f'<div class="avatar-init" style="display:none">{initials}</div>'
        )
    else:
        avatar_html = f'<div class="avatar-init">{initials}</div>'

    # Same-call badge
    same_call = meeting.get("same_call_as")
    same_badge = f'<span class="same-call-badge">↑ Same call as {h(same_call)}</span>\n          ' if same_call else ""

    # Buttons row
    meet_link     = meeting.get("meet_link") or ""
    meet_platform = h(meeting.get("meet_platform", "Join"))
    meet_emoji    = meeting.get("meet_emoji", "📹")
    topic_tag     = h(meeting.get("topic_tag", ""))

    join_btn = (f'<a class="btn btn-join" href="{h(meet_link)}" target="_blank">'
                f'{meet_emoji} {meet_platform} ↗</a>\n            ') if meet_link else ""
    li_btn   = (f'<a class="btn btn-li" href="{h(linkedin)}" target="_blank">LinkedIn ↗</a>\n            ') if linkedin else ""
    topic    = f'<span class="pill pill-t">{topic_tag}</span>' if topic_tag else ""

    # Blocks
    purpose_blk = ""
    if meeting.get("title") and not same_call:
        pass  # title shown via topic tag; meeting purpose comes from email_context or who block

    email_blk = ""
    if email_ctx:
        email_blk = f"""
          <div class="blk">
            <div class="blk-lbl">From your emails</div>
            <div class="blk-txt quote">{h(email_ctx)}</div>
          </div>"""

    time_label = h(meeting.get("time", ""))
    dur_label  = h(meeting.get("duration", "")) if not same_call else "Same call"

    return f"""
    <article class="mcard">
      <div class="mcard-grid">
        <div class="pcol">
          {avatar_html}
          <div class="time-box">
            <div class="time-main">{time_label}</div>
            <div class="time-dur">{dur_label}</div>
          </div>
        </div>
        <div class="mbody">
          {same_badge}<div class="pname">{name}</div>
          <div class="prole">{role}</div>
          <div class="btns">
            {join_btn}{li_btn}{topic}
          </div>{email_blk}
          <div class="mfooter">
            <div class="blk-lbl">Who {'they are' if '&' in name or '/' in name else 'they are' if same_call else 'they are'}</div>
            <div class="blk-txt" style="font-size:13px">{h(who)}</div>
          </div>
        </div>
      </div>
    </article>"""


def render_day_section(day: dict, idx: int, is_first: bool) -> str:
    day_id    = f"day{idx}"
    on_class  = " on" if is_first else ""
    label     = h(day.get("label", ""))
    summary   = day.get("summary", "")
    stats     = day.get("stats", {})
    weather   = day.get("weather", {})
    meetings  = day.get("meetings", [])

    # Stats pills
    ext_count  = stats.get("external", 0)
    int_count  = stats.get("internal", 0)
    ip_count   = stats.get("in_person", 0)
    off_count  = stats.get("offsite", 0)
    off_detail = h(stats.get("offsite_detail", ""))
    w_icon     = weather.get("icon", "🌤️")
    w_temp     = weather.get("temp_c", "")
    w_cond     = h(weather.get("condition", ""))
    w_next     = weather.get("next_day_temp_c", "")

    offsite_sub = f' <span class="offsite-sub">· {off_detail}</span>' if off_detail else ""
    next_temp   = f" · {w_next}°C tomorrow" if w_next and is_first else ""

    stats_html = f"""
      <div class="stat"><span class="stat-icon">🤝</span><span><strong>{ext_count}</strong> external</span></div>
      <div class="stat"><span class="stat-icon">👥</span><span><strong>{int_count}</strong> internal</span></div>
      <div class="stat"><span class="stat-icon">📍</span><span><strong>{ip_count}</strong> in-person</span></div>
      <div class="stat"><span class="stat-icon">🚗</span><span><strong>{off_count}</strong> offsite{offsite_sub}</span></div>
      <div class="stat-divider"></div>
      <div class="weather-stat"><span>{w_icon}</span><strong>{w_temp}°C</strong><span class="weather-cond">{w_cond}{next_temp}</span></div>"""

    # Meeting cards
    if meetings:
        cards_html = '\n'.join(
            render_person_card(m, p, idx)
            for m in meetings
            for p in m.get("people", [])
        )
        body_html = f"""
  <p class="slabel">External meetings · {label}</p>
  <div class="cards">{cards_html}
  </div>"""
    else:
        body_html = f"""
  <div class="empty">
    <div class="empty-title">No external meetings.</div>
    <p>No outside attendees found for {label}.</p>
  </div>"""

    return f"""
<section class="day{on_class}" id="d-{day_id}">
  <div class="chip">
    <div class="chip-summary">{h(summary)}</div>
    <div class="chip-stats">{stats_html}
    </div>
  </div>{body_html}
</section>"""


def render_html(data: dict, generated_at: str) -> str:
    days = data.get("days", [])

    # Tabs
    tabs = "\n".join(
        f'  <button class="tab{" on" if i == 0 else ""}" '
        f'onclick="sw(\'day{i}\')" id="t-day{i}">{h(d.get("tab_label", d.get("label", "")))}</button>'
        for i, d in enumerate(days)
    )

    # Sections
    sections = "\n".join(
        render_day_section(d, i, i == 0)
        for i, d in enumerate(days)
    )

    page_title = " & ".join(d.get("tab_label", "") for d in days[:2])

    return (HTML_TEMPLATE
            .replace("{{PAGE_TITLE}}", h(page_title))
            .replace("{{TABS}}", tabs)
            .replace("{{SECTIONS}}", sections)
            .replace("{{GENERATED_AT}}", h(generated_at)))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    today, tomorrow = get_target_dates()
    today_str    = today.isoformat()
    tomorrow_str = tomorrow.isoformat()
    today_label    = date_label(today)
    tomorrow_label = date_label(tomorrow)

    print(f"Generating briefing for {today_label} and {tomorrow_label}…")

    data = call_claude(today_str, tomorrow_str, today_label, tomorrow_label)

    now_mt = datetime.now(MOUNTAIN)
    generated_at = now_mt.strftime("%-I:%M %p, %A %B %-d %Y")

    html = render_html(data, generated_at)

    output_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ Written to index.html ({len(html):,} bytes)")


if __name__ == "__main__":
    main()
