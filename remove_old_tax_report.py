#!/usr/bin/env python3
"""Remove one unwanted October 28 tax briefing. Idempotent, preserves other posts."""
import json
from pathlib import Path

root=Path(__file__).resolve().parent
url='briefings/tax-2026-10-28.html'
posts_path=root/'posts.json'
if not posts_path.exists():
    raise SystemExit('posts.json not found; no changes made')
posts=json.loads(posts_path.read_text(encoding='utf-8'))
if not isinstance(posts,list):
    raise SystemExit('posts.json must be a JSON array')
removed=[p for p in posts if isinstance(p,dict) and p.get('category')=='tax' and p.get('url')==url]
keep=[p for p in posts if not (isinstance(p,dict) and p.get('category')=='tax' and p.get('url')==url)]
if removed:
    posts_path.write_text(json.dumps(keep, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
report=root/url
if report.is_file():
    report.unlink()
    print('DELETED:',url)
else:
    print('REPORT_NOT_FOUND:',url)
excluded_path=root/'tax_excluded_reports.json'
excluded=json.loads(excluded_path.read_text(encoding='utf-8')) if excluded_path.exists() else []
if not isinstance(excluded,list) or not all(isinstance(s,str) for s in excluded):
    raise SystemExit('tax_excluded_reports.json must be a list')
if '2026-10-28' not in excluded:
    excluded.append('2026-10-28')
    excluded_path.write_text(json.dumps(sorted(set(excluded)),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('REMOVED_POST_COUNT:',len(removed))
print('REMAINING_POSTS:',len(keep))
print('PRESERVED: tax_seen.json and all other briefings')
