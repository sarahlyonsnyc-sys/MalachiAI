#!/usr/bin/env python3
"""
CCC Tournament Scraper v3
=========================
Scrapes ALL pages of US Chess upcoming tournaments for every state,
plus Plan Ahead Calendar. Handles pagination (20+ pages per state).
Uses Claude API to parse batches. Auto-approves to Supabase.

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

# Priority states first, then rest alphabetically
PRIORITY = ['OH','FL','TX','CA','NY','PA','IL','MI','NC','VA','NJ','MO','IN','MD','GA','MA']
REST = ['AL','AK','AZ','AR','CO','CT','DE','DC','HI','ID','IA','KS','KY','LA','ME',
        'MN','MS','MT','NE','NV','NH','NM','ND','OK','OR','RI','SC','SD','TN','UT',
        'VT','WA','WV','WI','WY']
ALL_STATES = PRIORITY + REST

HEADERS = {'User-Agent': 'Mozilla/5.0 CCC-Tournament-Bot/3.0'}
MAX_PAGES_PER_STATE = 25  # Safety cap


def fetch_page(state, page=0):
    """Fetch one page of US Chess tournaments for a state."""
    url = (f"https://new.uschess.org/upcoming-tournaments"
           f"?combine="
           f"&field_event_address_administrative_area={state}"
           f"&field_online_event_value=2"
           f"&field_fide_rated_value=0"
           f"&page={page}")
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def has_tournaments(html):
    """Check if a page has actual tournament listings."""
    soup = BeautifulSoup(html, 'html.parser')
    # Look for tournament cards/articles
    entries = soup.find_all(['article', 'div'], class_=lambda c: c and ('views-row' in str(c) or 'node--type' in str(c)))
    if entries and len(entries) > 0:
        return True
    # Fallback: check for tournament titles (h3 links)
    links = soup.find_all('h3')
    return len(links) > 2  # more than just nav elements


def extract_text(html):
    """Extract tournament text from HTML."""
    soup = BeautifulSoup(html, 'html.parser')
    for t in soup(['script','style','nav','footer','header','form']): t.decompose()
    main = soup.find('main') or soup.find(class_='view-content') or soup
    entries = main.find_all(['article','div'], class_=lambda c: c and ('views-row' in str(c) or 'node--type' in str(c)))
    if entries:
        text = '\n\n---\n\n'.join(e.get_text(separator='\n', strip=True) for e in entries)
    else:
        text = main.get_text(separator='\n', strip=True)
    return text


def fetch_all_state_pages(state):
    """Fetch ALL pages of tournaments for a state, concatenate text."""
    all_text = ""
    page = 0
    
    while page < MAX_PAGES_PER_STATE:
        try:
            html = fetch_page(state, page)
            
            if not has_tournaments(html):
                break
            
            text = extract_text(html)
            if len(text.strip()) < 100:
                break
            
            all_text += f"\n\n=== PAGE {page + 1} ===\n\n" + text
            page += 1
            time.sleep(0.8)  # Be nice to US Chess
            
        except Exception as e:
            break
    
    return all_text, page


def parse_claude(raw, state, source):
    """Send text to Claude for parsing. Handle large batches by chunking."""
    if len(raw.strip()) < 100:
        return []
    
    # If text is very long, split into chunks and parse each
    chunks = []
    if len(raw) > 14000:
        # Split on page markers
        parts = raw.split('=== PAGE')
        current_chunk = ""
        for part in parts:
            if len(current_chunk) + len(part) > 13000:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = part
            else:
                current_chunk += part
        if current_chunk:
            chunks.append(current_chunk)
    else:
        chunks = [raw]
    
    all_tournaments = []
    
    for chunk in chunks:
        prompt = f"""Parse chess tournament listings from {source} (state: {state}).
Return JSON array. Each object:
- "name": string (tournament name)
- "city": string or null
- "state": "{state}"
- "date_start": "YYYY-MM-DD" (assume 2026 if year not shown)
- "date_end": "YYYY-MM-DD" or null (if multi-day)
- "format": "Classical"|"Rapid"|"Blitz"|"Scholastic"
  (K-12/scholastic/students/youth = "Scholastic", G/5 or G/3 = "Blitz", G/15-G/30 = "Rapid")
- "organizer": string or null
- "time_control": string or null (e.g. "G/90;d5")
- "entry_fee": string or null
- "notes": max 150 chars or null

Rules:
- Today is {date.today().isoformat()}. Skip events before today.
- Skip online-only events.
- For recurring weekly events, use the NEXT upcoming occurrence date.
- Each tournament should appear only ONCE even if listed multiple times.
- Return ONLY the JSON array, nothing else.

Text:
{chunk}"""
        
        try:
            r = claude.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt}]
            )
            t = r.content[0].text.strip()
            if t.startswith('```'): t = t.split('\n', 1)[1] if '\n' in t else t[3:]
            if t.endswith('```'): t = t[:-3]
            d = json.loads(t.strip())
            if isinstance(d, list):
                all_tournaments.extend(d)
            else:
                all_tournaments.append(d)
        except Exception as e:
            print(f"    Parse error on chunk: {e}")
    
    return all_tournaments


def get_existing():
    """Get dedup hashes for existing records."""
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
    """Push to staging table, skip dupes."""
    n = 0
    for t in tournaments:
        name = (t.get('name') or '').strip()
        ds = t.get('date_start', '')
        st = t.get('state', '')
        if not name or not ds: continue
        try:
            if datetime.strptime(ds, '%Y-%m-%d').date() < date.today(): continue
        except: continue
        dk = hashlib.md5(f"{name.lower()}|{ds}|{st}".encode()).hexdigest()
        if dk in existing: continue
        row = {
            'raw_name': name, 'raw_location': f"{t.get('city','')}, {st}", 'raw_date': ds,
            'parsed_name': name, 'parsed_city': t.get('city'), 'parsed_state': st if len(st) == 2 else None,
            'parsed_date_start': ds, 'parsed_date_end': t.get('date_end'),
            'parsed_format': t.get('format', 'Classical'), 'parsed_organizer': t.get('organizer'),
            'source': src, 'source_url': url,
            'raw_details': json.dumps({k: v for k, v in t.items() if k not in ('name','city','state','date_start','date_end','format','organizer') and v}),
            'status': 'pending'
        }
        try:
            sb.table('tournament_imports').insert(row).execute()
            existing.add(dk); n += 1
        except: pass
    return n


def auto_approve():
    """Promote pending imports to live."""
    try:
        pending = sb.table('tournament_imports').select('*').eq('status', 'pending').execute()
        if not pending.data: return 0
    except: return 0
    approved = 0
    for imp in pending.data:
        ex = sb.table('tournaments').select('id').eq('name', imp['parsed_name']).eq('date_start', imp['parsed_date_start']).execute()
        if ex.data and len(ex.data) > 0:
            sb.table('tournament_imports').update({'status': 'duplicate', 'reviewed_at': datetime.utcnow().isoformat()}).eq('id', imp['id']).execute()
            continue
        t = {
            'name': imp['parsed_name'], 'city': imp['parsed_city'], 'state': imp['parsed_state'],
            'date_start': imp['parsed_date_start'], 'date_end': imp['parsed_date_end'],
            'format': imp['parsed_format'] or 'Classical', 'organizer': imp['parsed_organizer'],
            'source': imp['source'], 'source_url': imp['source_url'], 'status': 'upcoming'
        }
        try:
            details = json.loads(imp.get('raw_details') or '{}')
            for f in ['time_control', 'entry_fee', 'registration_url', 'notes']:
                if details.get(f): t[f] = details[f]
        except: pass
        try:
            sb.table('tournaments').insert(t).execute()
            sb.table('tournament_imports').update({'status': 'approved', 'reviewed_at': datetime.utcnow().isoformat()}).eq('id', imp['id']).execute()
            approved += 1
        except:
            sb.table('tournament_imports').update({'status': 'error'}).eq('id', imp['id']).execute()
    return approved


def main():
    print("=" * 60)
    print(f"CCC Tournament Scraper v3 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Full pagination · {len(ALL_STATES)} states · All pages")
    print("=" * 60)

    existing = get_existing()
    print(f"📊 {len(existing)} existing records\n")

    total_found = 0
    total_new = 0
    total_pages = 0

    for i, st in enumerate(ALL_STATES):
        print(f"[{i+1}/{len(ALL_STATES)}] 📡 {st}", end=" ", flush=True)
        try:
            all_text, pages = fetch_all_state_pages(st)
            total_pages += pages
            
            if pages == 0 or len(all_text.strip()) < 200:
                print("— no events")
                continue
            
            print(f"({pages} pages)", end=" ", flush=True)
            
            tournaments = parse_claude(all_text, st, f"US Chess - {st}")
            total_found += len(tournaments)
            
            if tournaments:
                new = push_staging(tournaments, 'uschess', f'https://new.uschess.org/upcoming-tournaments?state={st}', existing)
                total_new += new
                print(f"— {len(tournaments)} found, {new} new")
            else:
                print("— 0 parsed")
                
        except Exception as e:
            print(f"— error: {e}")
            continue

    # Plan Ahead Calendar
    print(f"\n📡 Plan Ahead Calendar", end=" ", flush=True)
    try:
        r = requests.get("https://new.uschess.org/plan-ahead-calendar", headers=HEADERS, timeout=30)
        text = extract_text(r.text)
        if len(text.strip()) > 200:
            t = parse_claude(text, 'US', "Plan Ahead Calendar")
            total_found += len(t)
            new = push_staging(t, 'uschess_plan', 'https://new.uschess.org/plan-ahead-calendar', existing)
            total_new += new
            print(f"— {len(t)} found, {new} new")
    except Exception as e:
        print(f"— error: {e}")

    # ── STATE CHESS ORGANIZATIONS ──
    # These sites list local/regional tournaments that US Chess doesn't include
    STATE_ORGS = [
        # New Mexico
        {'url': 'https://www.newmexicochess.org/tournament-events', 'state': 'NM', 'name': 'NM Chess Organization', 'source': 'nmco'},
        {'url': 'https://learnerschess.org/cnmtcl/', 'state': 'NM', 'name': 'Learners Chess Academy NM', 'source': 'learners_chess'},
        {'url': 'https://www.kingregistration.com/tournaments/state/NM', 'state': 'NM', 'name': 'King Registration NM', 'source': 'king_reg'},
        # Ohio
        {'url': 'https://www.kingregistration.com/tournaments/state/OH', 'state': 'OH', 'name': 'King Registration OH', 'source': 'king_reg'},
        # Florida
        {'url': 'https://www.kingregistration.com/tournaments/state/FL', 'state': 'FL', 'name': 'King Registration FL', 'source': 'king_reg'},
        # Texas
        {'url': 'https://www.kingregistration.com/tournaments/state/TX', 'state': 'TX', 'name': 'King Registration TX', 'source': 'king_reg'},
        # California
        {'url': 'https://www.kingregistration.com/tournaments/state/CA', 'state': 'CA', 'name': 'King Registration CA', 'source': 'king_reg'},
        # New York
        {'url': 'https://www.kingregistration.com/tournaments/state/NY', 'state': 'NY', 'name': 'King Registration NY', 'source': 'king_reg'},
        # Pennsylvania
        {'url': 'https://www.kingregistration.com/tournaments/state/PA', 'state': 'PA', 'name': 'King Registration PA', 'source': 'king_reg'},
        # Illinois
        {'url': 'https://www.kingregistration.com/tournaments/state/IL', 'state': 'IL', 'name': 'King Registration IL', 'source': 'king_reg'},
    ]
    
    print(f"\n── State Chess Organizations ({len(STATE_ORGS)} sources) ──")
    for org in STATE_ORGS:
        print(f"📡 {org['name']}", end=" ", flush=True)
        try:
            r = requests.get(org['url'], headers=HEADERS, timeout=20)
            text = extract_text(r.text)
            if len(text.strip()) > 150:
                t = parse_claude(text, org['state'], org['name'])
                total_found += len(t)
                if t:
                    new = push_staging(t, org['source'], org['url'], existing)
                    total_new += new
                    print(f"— {len(t)} found, {new} new")
                else:
                    print("— 0 parsed")
            else:
                print("— no content")
            time.sleep(1)
        except Exception as e:
            print(f"— error: {e}")
            continue

    # Summary
    print(f"\n{'=' * 60}")
    print(f"📊 Scraped {total_pages} total pages across {len(ALL_STATES)} states")
    print(f"📊 Found {total_found} tournaments, {total_new} are new")

    if total_new > 0:
        print(f"\n✅ Auto-approving {total_new} imports...")
        a = auto_approve()
        print(f"✅ {a} tournaments now live!")
    else:
        print("\nNo new tournaments this run.")

    # Cleanup past events
    try:
        r = sb.table('tournaments').delete().lt('date_start', date.today().isoformat()).execute()
        if r.data and len(r.data) > 0:
            print(f"🧹 Removed {len(r.data)} past events")
    except: pass

    # Final count
    try:
        c = sb.table('tournaments').select('id', count='exact').execute()
        print(f"\n📊 Total live tournaments in database: {c.count}")
    except: pass

    print(f"{'=' * 60}\nDone!")


if __name__ == '__main__':
    main()
