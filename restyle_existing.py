#!/usr/bin/env python3
"""One-time HTML styling migration for existing generated pages; does not call any API."""
import pathlib, re, json
ROOT=pathlib.Path(__file__).resolve().parent
CSS='.impact{background:#F2F4F5;border:1px solid #E4E8E9;border-radius:10px;padding:16px 18px;margin:18px 0}.impact-title{font-size:13px;font-weight:800;color:#007985;margin-bottom:7px}.impact p{margin:0}'

def strip_links(s):
    s=re.sub(r'\s*\([^()\n]{1,100}\)\(https?://[^\s)]+\)', '',s)
    s=re.sub(r'\s*\(https?://[^\s)]+\)', '',s)
    s=re.sub(r'\s*https?://[^\s<>()]+','',s)
    return re.sub(r'[ \t]{2,}',' ',s).strip()


# Restrict changes to sentence endings. Never change document titles, URLs or HTML attributes.
SENTENCE_ENDINGS = [
    ("해야 한다", "해야 합니다"),
    ("할 필요가 있다", "할 필요가 있습니다"),
    ("할 수 있다", "할 수 있습니다"),
    ("할 수 없다", "할 수 없습니다"),
    ("것으로 보인다", "것으로 보입니다"),
    ("필요가 있다", "필요가 있습니다"),
    ("필요가 없다", "필요가 없습니다"),
    ("않는다", "않습니다"),
    ("있다", "있습니다"),
    ("없다", "없습니다"),
    ("된다", "됩니다"),
    ("한다", "합니다"),
    ("이다", "입니다"),
    ("였다", "였습니다"),
    ("됐다", "됐습니다"),
    ("했다", "했습니다"),
    ("혔다", "혔습니다"),
    ("았다", "았습니다"),
    ("었다", "었습니다"),
    ("냈다", "냈습니다"),
    ("졌다", "졌습니다"),
    ("쳤다", "쳤습니다"),
    ("났다", "났습니다"),
    ("봤다", "봤습니다"),
    ("갔다", "갔습니다"),
]
def formalize(text):
    """Conservative, non-AI plain-style to polite style conversion."""
    value=str(text or "")
    # Only convert explicit declarative sentence endings.
    for old, new in SENTENCE_ENDINGS:
        value = re.sub(re.escape(old)+r'(?=\s*(?:[.!?]|$))', new, value)
    return value

changed=0
for path in (ROOT/'briefings').glob('*.html'):
    raw=path.read_text(encoding='utf-8')
    text=raw
    # Edit only article body paragraphs; retain the actual link in <a href=...>.
    text=re.sub(r'(<article>.*?</article>)',lambda m:re.sub(r'<p>(.*?)</p>',lambda p:'<p>'+formalize(strip_links(p.group(1)))+'</p>',m.group(1), flags=re.S),text,flags=re.S)
    text=re.sub(r'<p>\s*<strong>실무 영향</strong>\s*(.*?)</p>',r'<div class="impact"><div class="impact-title">실무 영향 및 검토사항</div><p>\1</p></div>',text,flags=re.S)
    # Includes existing pages already restyled in the earlier run.
    text=re.sub(r'<p>(.*?)</p>', lambda m: '<p>'+formalize(m.group(1))+'</p>', text, flags=re.S)
    if '.impact-title{' not in text:
        text=text.replace('</style>',CSS+'</style>',1)
    if text!=raw:
        path.write_text(text,encoding='utf-8');changed+=1
print(f'Restyled existing briefing pages: {changed}')

# Also fix the summaries visible on the homepage.
posts_path=ROOT/'posts.json'
if posts_path.exists():
    posts=json.loads(posts_path.read_text(encoding='utf-8'))
    if not isinstance(posts,list):
        raise ValueError('posts.json must be a list')
    changed_posts=0
    for post in posts:
        if isinstance(post,dict) and isinstance(post.get('summary'),str):
            new=formalize(post['summary'])
            if post['summary']!=new:
                post['summary']=new
                changed_posts+=1
    if changed_posts:
        posts_path.write_text(json.dumps(posts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Updated homepage summaries: {changed_posts}')
