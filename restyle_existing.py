#!/usr/bin/env python3
"""One-time HTML styling migration for existing generated pages; does not call any API."""
import pathlib, re
ROOT=pathlib.Path(__file__).resolve().parent
CSS='.impact{background:#F2F4F5;border:1px solid #E4E8E9;border-radius:10px;padding:16px 18px;margin:18px 0}.impact-title{font-size:13px;font-weight:800;color:#007985;margin-bottom:7px}.impact p{margin:0}'

def strip_links(s):
    s=re.sub(r'\s*\([^()\n]{1,100}\)\(https?://[^\s)]+\)', '',s)
    s=re.sub(r'\s*\(https?://[^\s)]+\)', '',s)
    s=re.sub(r'\s*https?://[^\s<>()]+','',s)
    return re.sub(r'[ \t]{2,}',' ',s).strip()

changed=0
for path in (ROOT/'briefings').glob('*.html'):
    raw=path.read_text(encoding='utf-8')
    text=raw
    # Edit only article body paragraphs; retain the actual link in <a href=...>.
    text=re.sub(r'(<article>.*?</article>)',lambda m:re.sub(r'<p>(.*?)</p>',lambda p:'<p>'+strip_links(p.group(1))+'</p>',m.group(1), flags=re.S),text,flags=re.S)
    text=re.sub(r'<p>\s*<strong>실무 영향</strong>\s*(.*?)</p>',r'<div class="impact"><div class="impact-title">실무 영향 및 검토사항</div><p>\1</p></div>',text,flags=re.S)
    if '.impact-title{' not in text:
        text=text.replace('</style>',CSS+'</style>',1)
    if text!=raw:
        path.write_text(text,encoding='utf-8');changed+=1
print(f'Restyled existing briefing pages: {changed}')
