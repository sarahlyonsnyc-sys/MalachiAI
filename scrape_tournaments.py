#!/usr/bin/env python3
"""
CCC Tournament Scraper v2
=========================
Scrapes US Chess upcoming tournaments STATE BY STATE for comprehensive coverage,
plus the Plan Ahead Calendar for major events.
Uses Claude API to parse HTML into structured JSON.
Pushes to Supabase tournament_imports, then auto-approves.

Environment variables:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY
  ANTHROPIC_API_KEY
"""

import os, json, hashlib, time
from datetime import datetime, date
import requests
from bs4 import BeautifulSoup
import anthropic
from supabase import create_client

SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
ANTHROPIC_KEY = os.environ['ANTHROPIC_API_KEY']

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

ALL_STATES = [
    'OH','FL','TX','CA','NY','PA','IL','MI','NC','VA','NJ','MO','IN','MD','GA','MA',
    'AL','AK','AZ','AR','CO','CT','DE','DC','HI','ID','IA','KS','KY','LA','ME',
    'MN','MS','MT','NE','NV','NH','NM','ND','OK','OR','RI','SC','SD','TN','UT',
    'VT','WA','WV','WI','WY'
]

HEADERS = {'User-Agent': 'Mozilla/5.0 CCC-Tournament-Bot/2.0'}

def fetch_state(st):
    url = (f"https://new.uschess.org/upcoming-tournaments"
           f"?combine=&field_event_address_administrative_area={st}"
           f"&field_online_event_value=2&field_fide_rated_value=0")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def fetch_plan_ahead():
    r = requests.get("https://new.uschess.org/plan-ahead-calendar", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text

def extract_text(html):
    soup = BeautifulSoup(html, 'html.parser')
    for t in soup(['script','style','nav','footer','header','form']): t.decompose()
    main = soup.find('main') or soup.find(class_='view-content') or soup
    entries = main.find_all(['article','div'], class_=lambda c: c and ('views-row' in str(c) or 'node--type' in str(c)))
    if entries:
        text = '\n\n---\n\n'.join(e.get_text(separator='\n', strip=True) for e in entries)
    else:
        text = main.get_text(separator='\n', strip=True)
    return text[:15000] if len(text) > 15000 else text

def parse_claude(raw, state, source):
    if len(raw.strip()) < 100: return []
    prompt = f"""Parse chess tournament listings from {source} (state: {state}).
Return JSON array. Each object:
- "name": string
- "city": string or null
- "state": "{state}"
- "date_start": "YYYY-MM-DD" (assume 2026 if year missing)
- "date_end": "YYYY-MM-DD" or null
- "format": "Classical"|"Rapid"|"Blitz"|"Scholastic" (K-12/scholastic/students = Scholastic)
- "organizer": string or null
- "time_control": string or null
- "entry_fee": string or null
- "notes": max 200 chars or null
Skip past events (before today {date.today().isoformat()}).
Skip online-only events.
If recurring weekly, use next upcoming date.
Return ONLY JSON array.

Text:
{raw}"""
    try:
        r = claude.messages.create(model="claude-sonnet-4-20250514", max_tokens=4000, messages=[{"role":"user","content":prompt}])
        t = r.content[0].text.strip()
        if t.startswith('```'): t = t.split('\n',1)[1] if '\n' in t else t[3:]
        if t.endswith('```'): t = t[:-3]
        d = json.loads(t.strip())
        return d if isinstance(d, list) else [d]
    except Exception as e:
        print(f"    Parse error: {e}")
        return []

def get_existing():
    keys = set()
    try:
        for row in (sb.table('tournaments').select('name,date_start,state').execute().data or []):
            k = f"{(row.get('name') or '').lower()}|{row.get('date_start') or ''}|{row.get('state') or ''}"
            keys.add(hashlib.md5(k.encode()).hexdigest())
    except: pass
    try:
        for row in (sb.table('tournament_imports').select('parsed_name,parsed_date_start,parsed_state').execute().data or []):
            k = f"{(row.get('parsed_name') or '').lower()}|{row.get('parsed_date_start') or ''}|{row.get('parsed_state') or ''}"
            keys.add(hashlib.md5(k.encode()).hexdigest())
    except: pass
    return keys

def push_staging(tournaments, src, url, existing):
    n = 0
    for t in tournaments:
        name = (t.get('name') or '').strip()
        ds = t.get('date_start','')
        st = t.get('state','')
        if not name or not ds: continue
        try:
            if datetime.strptime(ds, '%Y-%m-%d').date() < date.today(): continue
        except: continue
        dk = hashlib.md5(f"{name.lower()}|{ds}|{st}".encode()).hexdigest()
        if dk in existing: continue
        row = {
            'raw_name':name, 'raw_location':f"{t.get('city','')}, {st}", 'raw_date':ds,
            'parsed_name':name, 'parsed_city':t.get('city'), 'parsed_state':st if len(st)==2 else None,
            'parsed_date_start':ds, 'parsed_date_end':t.get('date_end'),
            'parsed_format':t.get('format','Classical'), 'parsed_organizer':t.get('organizer'),
            'source':src, 'source_url':url,
            'raw_details':json.dumps({k:v for k,v in t.items() if k not in ('name','city','state','date_start','date_end','format','organizer') and v}),
            'status':'pending'
        }
        try:
            sb.table('tournament_imports').insert(row).execute()
            existing.add(dk); n += 1
        except: pass
    return n

def auto_approve():
    try:
        pending = sb.table('tournament_imports').select('*').eq('status','pending').execute()
        if not pending.data: return 0
    except: return 0
    approved = 0
    for imp in pending.data:
        ex = sb.table('tournaments').select('id').eq('name',imp['parsed_name']).eq('date_start',imp['parsed_date_start']).execute()
        if ex.data and len(ex.data) > 0:
            sb.table('tournament_imports').update({'status':'duplicate','reviewed_at':datetime.utcnow().isoformat()}).eq('id',imp['id']).execute()
            continue
        t = {'name':imp['parsed_name'],'city':imp['parsed_city'],'state':imp['parsed_state'],
             'date_start':imp['parsed_date_start'],'date_end':imp['parsed_date_end'],
             'format':imp['parsed_format'] or 'Classical','organizer':imp['parsed_organizer'],
             'source':imp['source'],'source_url':imp['source_url'],'status':'upcoming'}
        try:
            details = json.loads(imp.get('raw_details') or '{}')
            for f in ['time_control','entry_fee','registration_url','notes']:
                if details.get(f): t[f] = details[f]
        except: pass
        try:
            sb.table('tournaments').insert(t).execute()
            sb.table('tournament_imports').update({'status':'approved','reviewed_at':datetime.utcnow().isoformat()}).eq('id',imp['id']).execute()
            approved += 1
        except:
            sb.table('tournament_imports').update({'status':'error'}).eq('id',imp['id']).execute()
    return approved

def main():
    print("="*60)
    print(f"CCC Tournament Scraper v2 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Scanning {len(ALL_STATES)} states + Plan Ahead Calendar")
    print("="*60)

    existing = get_existing()
    print(f"📊 {len(existing)} existing records\n")

    total_found = 0; total_new = 0; active_states = 0

    for i, st in enumerate(ALL_STATES):
        print(f"[{i+1}/{len(ALL_STATES)}] 📡 {st}", end=" ")
        try:
            html = fetch_state(st)
            text = extract_text(html)
            if len(text.strip()) < 200:
                print("— no events"); continue
            tournaments = parse_claude(text, st, f"US Chess - {st}")
            total_found += len(tournaments)
            if tournaments:
                active_states += 1
                new = push_staging(tournaments, 'uschess', f'https://new.uschess.org/upcoming-tournaments?state={st}', existing)
                total_new += new
                print(f"— {len(tournaments)} found, {new} new")
            else:
                print("— 0 parsed")
            time.sleep(1)
        except Exception as e:
            print(f"— error: {e}"); continue

    # Plan Ahead Calendar
    print(f"\n📡 Plan Ahead Calendar", end=" ")
    try:
        html = fetch_plan_ahead()
        text = extract_text(html)
        if len(text.strip()) > 200:
            t = parse_claude(text, 'US', "US Chess Plan Ahead")
            total_found += len(t)
            new = push_staging(t, 'uschess_plan', 'https://new.uschess.org/plan-ahead-calendar', existing)
            total_new += new
            print(f"— {len(t)} found, {new} new")
    except Exception as e:
        print(f"— error: {e}")

    print(f"\n{'='*60}")
    print(f"📊 Total: {total_found} found across {active_states} states, {total_new} new")

    if total_new > 0:
        print(f"✅ Auto-approving...")
        a = auto_approve()
        print(f"✅ {a} tournaments now live!")

    # Cleanup past
    try:
        r = sb.table('tournaments').delete().lt('date_start', date.today().isoformat()).execute()
        if r.data: print(f"🧹 Removed {len(r.data)} past events")
    except: pass

    try:
        c = sb.table('tournaments').select('id', count='exact').execute()
        print(f"\n📊 Total live tournaments: {c.count}")
    except: pass

    print(f"{'='*60}\nDone!")

if __name__ == '__main__':
    main()
