#!/usr/bin/env python3
"""
CCC Tournament Scraper
======================
Scrapes US Chess upcoming tournaments + Plan Ahead Calendar,
uses Claude API to parse messy HTML into structured JSON,
pushes to Supabase tournament_imports, then auto-approves new events.

Can be run locally or via GitHub Actions.

Environment variables needed:
  SUPABASE_URL
  SUPABASE_SERVICE_KEY  (service_role, not anon)
  ANTHROPIC_API_KEY
"""

import os
import json
import hashlib
from datetime import datetime, date

import requests
from bs4 import BeautifulSoup
import anthropic
from supabase import create_client

# ── CONFIG ──
SUPABASE_URL = os.environ['SUPABASE_URL']
SUPABASE_KEY = os.environ['SUPABASE_SERVICE_KEY']
ANTHROPIC_KEY = os.environ['ANTHROPIC_API_KEY']

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

SOURCES = [
    {
        'name': 'US Chess Upcoming Tournaments',
        'url': 'https://new.uschess.org/upcoming-tournaments',
        'source_key': 'uschess',
    },
    {
        'name': 'US Chess Plan Ahead Calendar',
        'url': 'https://new.uschess.org/plan-ahead-calendar',
        'source_key': 'uschess_plan',
    },
]

US_STATES = {
    'Alabama':'AL','Alaska':'AK','Arizona':'AZ','Arkansas':'AR','California':'CA',
    'Colorado':'CO','Connecticut':'CT','Delaware':'DE','Florida':'FL','Georgia':'GA',
    'Hawaii':'HI','Idaho':'ID','Illinois':'IL','Indiana':'IN','Iowa':'IA',
    'Kansas':'KS','Kentucky':'KY','Louisiana':'LA','Maine':'ME','Maryland':'MD',
    'Massachusetts':'MA','Michigan':'MI','Minnesota':'MN','Mississippi':'MS','Missouri':'MO',
    'Montana':'MT','Nebraska':'NE','Nevada':'NV','New Hampshire':'NH','New Jersey':'NJ',
    'New Mexico':'NM','New York':'NY','North Carolina':'NC','North Dakota':'ND','Ohio':'OH',
    'Oklahoma':'OK','Oregon':'OR','Pennsylvania':'PA','Rhode Island':'RI','South Carolina':'SC',
    'South Dakota':'SD','Tennessee':'TN','Texas':'TX','Utah':'UT','Vermont':'VT',
    'Virginia':'VA','Washington':'WA','West Virginia':'WV','Wisconsin':'WI','Wyoming':'WY',
    # Common abbreviations
    'AL':'AL','AK':'AK','AZ':'AZ','AR':'AR','CA':'CA','CO':'CO','CT':'CT','DE':'DE',
    'FL':'FL','GA':'GA','HI':'HI','ID':'ID','IL':'IL','IN':'IN','IA':'IA','KS':'KS',
    'KY':'KY','LA':'LA','ME':'ME','MD':'MD','MA':'MA','MI':'MI','MN':'MN','MS':'MS',
    'MO':'MO','MT':'MT','NE':'NE','NV':'NV','NH':'NH','NJ':'NJ','NM':'NM','NY':'NY',
    'NC':'NC','ND':'ND','OH':'OH','OK':'OK','OR':'OR','PA':'PA','RI':'RI','SC':'SC',
    'SD':'SD','TN':'TN','TX':'TX','UT':'UT','VT':'VT','VA':'VA','WA':'WA','WV':'WV',
    'WI':'WI','WY':'WY',
}


def fetch_page(url: str) -> str:
    """Fetch a web page with a browser-like user agent."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) CCC-Tournament-Bot/1.0'
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def extract_text_content(html: str) -> str:
    """Strip HTML to get raw text content for Claude to parse."""
    soup = BeautifulSoup(html, 'html.parser')
    
    # Remove script/style tags
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    
    # Get the main content area
    main = soup.find('main') or soup.find('article') or soup.find(class_='content') or soup
    text = main.get_text(separator='\n', strip=True)
    
    # Limit to ~8000 chars to fit in Claude context efficiently
    if len(text) > 12000:
        text = text[:12000] + '\n[TRUNCATED]'
    
    return text


def parse_with_claude(raw_text: str, source_name: str) -> list[dict]:
    """Use Claude to parse raw tournament text into structured JSON."""
    
    prompt = f"""You are parsing chess tournament listings scraped from {source_name}.

Extract every individual tournament from this text and return a JSON array.
Each tournament should have these fields:
- "name": tournament name (string)
- "city": city name (string or null)
- "state": 2-letter US state code like "OH", "FL", "TX" (string or null)  
- "date_start": start date in YYYY-MM-DD format (string)
- "date_end": end date in YYYY-MM-DD format if multi-day, null if single day
- "format": one of "Classical", "Rapid", "Blitz", "Scholastic", "Open" (best guess)
- "organizer": organizing body if mentioned (string or null)
- "time_control": time control if mentioned, like "G/90;d5" (string or null)
- "entry_fee": entry fee if mentioned (string or null)
- "registration_url": registration URL if present (string or null)
- "notes": any other notable info (string or null)

Rules:
- If a tournament says "K-12", "K-6", "K-8", "scholastic", "students", set format to "Scholastic"
- If it mentions "rapid" or time controls under 30 min, set format to "Rapid"  
- If it mentions "blitz" or time controls under 10 min, set format to "Blitz"
- Otherwise default format to "Classical"
- For state codes, convert full state names to 2-letter codes (Ohio → OH, Florida → FL)
- Only include future tournaments (2026 and beyond)
- Skip any entries that aren't actual tournaments (ads, articles, etc.)

Return ONLY the JSON array. No markdown, no explanation, no backticks.

Raw text:
{raw_text}"""

    response = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    text = response.content[0].text.strip()
    
    # Clean up any markdown fencing Claude might add despite instructions
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else text[3:]
    if text.endswith('```'):
        text = text[:-3]
    text = text.strip()
    
    try:
        tournaments = json.loads(text)
        if not isinstance(tournaments, list):
            tournaments = [tournaments]
        return tournaments
    except json.JSONDecodeError as e:
        print(f"  ⚠ Claude returned invalid JSON: {e}")
        print(f"  Raw response: {text[:500]}")
        return []


def dedup_key(t: dict) -> str:
    """Generate a deduplication hash for a tournament."""
    raw = f"{t.get('name','').lower().strip()}|{t.get('date_start','')}|{t.get('state','')}"
    return hashlib.md5(raw.encode()).hexdigest()


def push_to_staging(tournaments: list[dict], source_key: str, source_url: str):
    """Push parsed tournaments to tournament_imports staging table."""
    
    # Get existing imports to avoid duplicates
    existing = sb.table('tournament_imports').select('parsed_name,parsed_date_start,parsed_state').execute()
    existing_keys = set()
    for e in (existing.data or []):
        key = f"{(e.get('parsed_name') or '').lower().strip()}|{e.get('parsed_date_start') or ''}|{e.get('parsed_state') or ''}"
        existing_keys.add(hashlib.md5(key.encode()).hexdigest())
    
    # Also get existing live tournaments
    live = sb.table('tournaments').select('name,date_start,state').execute()
    for e in (live.data or []):
        key = f"{(e.get('name') or '').lower().strip()}|{e.get('date_start') or ''}|{e.get('state') or ''}"
        existing_keys.add(hashlib.md5(key.encode()).hexdigest())
    
    new_count = 0
    skip_count = 0
    
    for t in tournaments:
        dk = dedup_key(t)
        if dk in existing_keys:
            skip_count += 1
            continue
        
        # Validate date
        try:
            d = datetime.strptime(t.get('date_start', ''), '%Y-%m-%d').date()
            if d < date.today():
                skip_count += 1
                continue
        except (ValueError, TypeError):
            skip_count += 1
            continue
        
        # Normalize state code
        state = t.get('state', '')
        if state and len(state) > 2:
            state = US_STATES.get(state, US_STATES.get(state.title(), state[:2].upper()))
        
        row = {
            'raw_name': t.get('name'),
            'raw_location': f"{t.get('city', '')}, {state}",
            'raw_date': t.get('date_start'),
            'parsed_name': t.get('name'),
            'parsed_city': t.get('city'),
            'parsed_state': state if state and len(state) == 2 else None,
            'parsed_date_start': t.get('date_start'),
            'parsed_date_end': t.get('date_end'),
            'parsed_format': t.get('format', 'Classical'),
            'parsed_organizer': t.get('organizer'),
            'source': source_key,
            'source_url': source_url,
            'raw_details': json.dumps({k: v for k, v in t.items() if k not in ('name','city','state','date_start','date_end','format','organizer')}),
            'status': 'pending',
        }
        
        try:
            sb.table('tournament_imports').insert(row).execute()
            new_count += 1
            existing_keys.add(dk)
        except Exception as e:
            print(f"  ⚠ Insert failed for {t.get('name')}: {e}")
    
    print(f"  → {new_count} new imports, {skip_count} skipped (dupe/past)")
    return new_count


def auto_approve_imports():
    """
    Promote all pending imports to live tournaments table.
    In a more cautious setup, you'd review first. 
    This runs automatically since Claude already cleaned the data.
    """
    pending = sb.table('tournament_imports').select('*').eq('status', 'pending').execute()
    
    if not pending.data:
        print("No pending imports to approve.")
        return 0
    
    approved = 0
    for imp in pending.data:
        tournament = {
            'name': imp['parsed_name'],
            'city': imp['parsed_city'],
            'state': imp['parsed_state'],
            'date_start': imp['parsed_date_start'],
            'date_end': imp['parsed_date_end'],
            'format': imp['parsed_format'] or 'Classical',
            'organizer': imp['parsed_organizer'],
            'source': imp['source'],
            'source_url': imp['source_url'],
            'status': 'upcoming',
        }
        
        # Parse extra details
        try:
            details = json.loads(imp.get('raw_details') or '{}')
            if details.get('time_control'):
                tournament['time_control'] = details['time_control']
            if details.get('entry_fee'):
                tournament['entry_fee'] = details['entry_fee']
            if details.get('registration_url'):
                tournament['registration_url'] = details['registration_url']
            if details.get('notes'):
                tournament['notes'] = details['notes']
        except (json.JSONDecodeError, TypeError):
            pass
        
        try:
            result = sb.table('tournaments').insert(tournament).execute()
            # Mark import as approved
            sb.table('tournament_imports').update({
                'status': 'approved',
                'reviewed_at': datetime.utcnow().isoformat(),
            }).eq('id', imp['id']).execute()
            approved += 1
        except Exception as e:
            print(f"  ⚠ Approve failed for {imp['parsed_name']}: {e}")
            # Mark as duplicate if it's a unique constraint violation
            if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
                sb.table('tournament_imports').update({'status': 'duplicate'}).eq('id', imp['id']).execute()
    
    print(f"  ✓ Auto-approved {approved} tournaments into live table")
    return approved


def cleanup_past_tournaments():
    """Remove tournaments that have already passed."""
    today = date.today().isoformat()
    result = sb.table('tournaments').delete().lt('date_start', today).execute()
    removed = len(result.data) if result.data else 0
    if removed > 0:
        print(f"  🧹 Cleaned up {removed} past tournaments")


def main():
    print("=" * 60)
    print(f"CCC Tournament Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    total_new = 0
    
    for source in SOURCES:
        print(f"\n📡 Fetching: {source['name']}")
        print(f"   URL: {source['url']}")
        
        try:
            html = fetch_page(source['url'])
            print(f"   Got {len(html)} bytes")
            
            text = extract_text_content(html)
            print(f"   Extracted {len(text)} chars of text content")
            
            print(f"   🤖 Sending to Claude for parsing...")
            tournaments = parse_with_claude(text, source['name'])
            print(f"   Claude found {len(tournaments)} tournaments")
            
            if tournaments:
                new = push_to_staging(tournaments, source['source_key'], source['url'])
                total_new += new
                
        except Exception as e:
            print(f"   ❌ Error: {e}")
            continue
    
    # Auto-approve all pending imports
    if total_new > 0:
        print(f"\n✅ Auto-approving {total_new} new imports...")
        auto_approve_imports()
    else:
        print("\nNo new tournaments found this run.")
    
    # Cleanup past events
    print("\n🧹 Cleaning up past events...")
    cleanup_past_tournaments()
    
    print(f"\n{'=' * 60}")
    print(f"Done! {total_new} new tournaments added.")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
