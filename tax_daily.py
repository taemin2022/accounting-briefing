#!/usr/bin/env python3
"""Daily Korean tax briefing. Official RSS + verified OpenAI web search.

No LAW_API_OC is needed. --check performs RSS-only connectivity tests (no AI charge).
Standard library only. Fail closed on uncertain provenance or missing publication date.
"""
import argparse
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OPENAI = 'https://api.openai.com/v1/responses'
FEEDS = {
    '재정경제부 보도·참고자료': 'https://mofe.go.kr/com/detailRssTagService.do?bbsId=MOSFBBS_000000000028',
    '재정경제부 세법해석사례': 'https://mofe.go.kr/com/detailRssTagService.do?bbsId=MOSFBBS_000000000237',
    '재정경제부 입법예고': 'https://mofe.go.kr/com/detailRssTagService.do?bbsId=MOSFBBS_000000000055',
    '관세청 보도자료': 'https://www.customs.go.kr/kcs/selectBoardRss.do?mi=15265&bbsId=1362',
    '관세청 행정규칙 예고': 'https://www.customs.go.kr/kcs/selectBoardRss.do?mi=2897&bbsId=1366',
}
ALLOW_DOMAINS = ('mofe.go.kr', 'customs.go.kr', 'nts.go.kr', 'taxlaw.nts.go.kr')
TAX_TERMS = ('세법','세금','세액','법인세','부가가치세','부가세','원천징수','원천세','조세','세제',
             '세무','과세','비과세','면세','환급','공제','신고','소득세','상속세','증여세','관세',
             '통관','FTA','국제조세','이전가격','납세','국세','수입신고','수출신고','사전심사', '조세심판')
MAX_NEW = 24

def clean(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]*>', ' ', str(s or '')))).strip()

def tag(node):
    return node.tag.rsplit('}', 1)[-1]

def child_text(node, *names):
    for name in names:
        for ch in node:
            if tag(ch).lower() == name.lower().split(':')[-1]:
                value = ''.join(ch.itertext()).strip()
                if value:
                    return value
    return ''

def safe_url(raw):
    url = html.unescape(str(raw or '')).strip()
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    try:
        p = urllib.parse.urlsplit(url)
        host = (p.hostname or '').lower()
        if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
            return ''
        if not any(host == d or host.endswith('.' + d) for d in ALLOW_DOMAINS):
            return ''
        return url
    except ValueError:
        return ''

def canonical(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(('https', (p.hostname or '').lower(), p.path.rstrip('/'), p.query, ''))

def fetch(url, max_bytes=2_000_000):
    req = urllib.request.Request(url, headers={'User-Agent':'AccountingBriefingRSS/1.0',
                                              'Accept':'application/rss+xml,application/atom+xml,application/xml,text/xml,*/*'})
    with urllib.request.urlopen(req, timeout=22) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise RuntimeError('RSS response size exceeded limit')
    return data

def parse_date(value):
    v = clean(value)
    if not v:
        return ''
    try:
        d = email.utils.parsedate_to_datetime(v)
        if d:
            if d.tzinfo is None:
                d = d.replace(tzinfo=ZoneInfo('Asia/Seoul'))
            return d.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    m = re.search(r'(20\d{2})[-./년 ]+(\d{1,2})[-./월 ]+(\d{1,2})', v)
    if m:
        try: return dt.date(*map(int, m.groups())).isoformat()
        except ValueError: pass
    try:
        d=dt.datetime.fromisoformat(v.replace('Z','+00:00'))
        if d.tzinfo is None: d=d.replace(tzinfo=ZoneInfo('Asia/Seoul'))
        return d.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
    except ValueError:
        return ''

def rss_rows(source, url):
    data=fetch(url)
    root=ET.fromstring(data)
    entries=[x for x in root.iter() if tag(x).lower() in ('item','entry')]
    if tag(root).lower() not in ('rss','rdf','feed') and not entries:
        raise RuntimeError(f'Not an RSS/Atom document: root={tag(root)}')
    result=[]
    for x in entries:
        title=clean(child_text(x,'title'))
        link=child_text(x,'link')
        if not link:
            for ch in x:
                if tag(ch).lower()=='link' and ch.attrib.get('href'):
                    link=ch.attrib['href'];break
        link=safe_url(link)
        date=parse_date(child_text(x,'pubDate','published','updated','date','dc:date'))
        desc=clean(child_text(x,'description','summary','content','encoded'))
        if title and link and date:
            result.append(dict(id=hashlib.sha256(canonical(link).encode()).hexdigest(),
                               title=title,date=date,url=link,source=source,excerpt=desc[:1300]))
    return len(entries),result

def get_existing():
    fp=ROOT/'tax_seen.json'
    if not fp.exists(): return set()
    obj=json.loads(fp.read_text(encoding='utf-8'))
    if not isinstance(obj,list) or not all(isinstance(x,str) for x in obj):
        raise RuntimeError('tax_seen.json is malformed')
    return set(obj)

def model_output(payload):
    text='\n'.join(c.get('text','') for o in payload.get('output',[]) if o.get('type')=='message'
                   for c in o.get('content',[]) if c.get('type')=='output_text').strip()
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text, flags=re.I).strip()
    try: return json.loads(text)
    except json.JSONDecodeError: raise RuntimeError('OpenAI did not return valid JSON') from None

def ask_ai(prompt, search=False):
    secret=os.environ.get('OPENAI_API_KEY','').strip()
    if not secret: raise RuntimeError('OPENAI_API_KEY is missing')
    req_data={'model':os.environ.get('OPENAI_MODEL','gpt-6.1-sol'),'input':prompt,'max_output_tokens':9500}
    if search:
        req_data['tools']=[{'type':'web_search'}]
    req=urllib.request.Request(OPENAI,data=json.dumps(req_data,ensure_ascii=False).encode('utf-8'),
           method='POST',headers={'Authorization':'Bearer '+secret,'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=230) as r: return model_output(json.load(r))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'OpenAI request failed: HTTP {e.code}') from None

def verify_official_link(url):
    if not safe_url(url): return False
    try:
        # Never follow redirects to offsite domains; validate final destination separately.
        req=urllib.request.Request(url, headers={'User-Agent':'AccountingBriefingRSS/1.0'}, method='GET')
        with urllib.request.urlopen(req,timeout=16) as r:
            return bool(safe_url(r.geturl()) and r.status == 200)
    except (urllib.error.URLError, TimeoutError, ValueError):
        return False

def search_nts(start,end):
    prompt=f'''한국시간 {start}~{end} 기간에 **공식 홈페이지에 게시된** 법인세·부가가치세·세법개정·국세청 예규·조세심판례의 신규 자료를 웹 검색하시오.
검색 대상은 nts.go.kr 및 taxlaw.nts.go.kr 두 공식 도메인만 사용하시오.
반드시 공식 페이지의 게시일이 대상 기간에 속하는 자료만 포함. 게시일이 불명확하면 제외. 다른 사이트의 재인용은 제외.
최대 8건. 검색결과에 없는 문서나 링크를 생성하지 마십시오.
제목·게시일·원문 URL·원문으로 확인한 짧은 내용만 JSON으로 제출:
{{"items":[{{"title":"...","date":"YYYY-MM-DD","url":"https://...","excerpt":"..."}}]}}'''
    result=ask_ai(prompt,search=True)
    rows=[]
    for x in result.get('items',[]):
        if not isinstance(x,dict):continue
        url=safe_url(x.get('url',''))
        date=parse_date(x.get('date',''))
        title=clean(x.get('title',''))
        if not url or not date or not start<=date<=end or not title:continue
        host=urllib.parse.urlsplit(url).hostname or ''
        if not (host=='nts.go.kr' or host.endswith('.nts.go.kr')): continue
        if not verify_official_link(url):continue
        rows.append(dict(id=hashlib.sha256(canonical(url).encode()).hexdigest(),
                         title=title,date=date,url=url,source='국세청·국세법령정보시스템',
                         excerpt=clean(x.get('excerpt',''))[:1000]))
    print(f'NTS official web-search verified candidates: {len(rows)}',flush=True)
    return rows

def analyze(records):
    prompt='''다음은 공식 기관 RSS 및 공개 원문 검색으로 확인한 세무 관련 후보입니다. 메타데이터·발췌 범위를 넘어 사실을 지어내지 마세요.
법령 개정의 확정 여부, 유권해석의 결론·시행일 등 원문에 없다면 확정적으로 쓰지 마세요.
기업 법인세/부가가치세/관세 실무 관련성을 중심으로 한국어 존댓말 2~4문장의 요약과 2~3문장의 검토사항을 작성하세요.
원문에 충분한 사실이 없다면 확인이 필요한 사항으로 명시하세요. 전문 보고서처럼 간결하고 유용하게 작성하세요.
JSON만 반환: {"summary":"당일 전체 동향 요약", "items":[{"id":"...", "summary":"...", "implication":"..."}]}\n자료:\n'''+json.dumps(records,ensure_ascii=False)
    result=ask_ai(prompt)
    mapped={r['id']:r for r in result.get('items',[]) if isinstance(r,dict) and isinstance(r.get('id'),str)}
    if set(mapped)!={r['id'] for r in records}:raise RuntimeError('AI output IDs did not match candidates; report not published')
    items=[]
    for r in records:
        x=mapped[r['id']]
        if not clean(x.get('summary','')) or not clean(x.get('implication','')):
            raise RuntimeError('AI left empty analysis text')
        items.append(dict(title=r['title'],date=r['date'],url=r['url'],summary=clean(x['summary']),implication=clean(x['implication'])))
    overall=clean(result.get('summary',''))
    if not overall:raise RuntimeError('AI returned empty overall summary')
    return overall,items

def publish(today,start,end,summary,items,ids):
    # Existing site-compatible report renderer from briefing.py.
    from briefing import page,CATEGORIES
    CATEGORIES['tax']='세무 일일 브리핑'
    relative=f'briefings/tax-{today.isoformat()}.html'
    dst=ROOT/relative
    if dst.exists():raise RuntimeError('Today\'s tax report already exists; refusing overwrite')
    posts_file=ROOT/'posts.json'
    posts=json.loads(posts_file.read_text(encoding='utf-8')) if posts_file.exists() else []
    if not isinstance(posts,list):raise RuntimeError('posts.json must be an array')
    report=page('tax',f'세무 일일 브리핑 | {today.isoformat()}',today.isoformat(),start.isoformat(),end.isoformat(),summary,items)
    dst.parent.mkdir(parents=True,exist_ok=True)
    dst.write_text(report,encoding='utf-8')
    posts.insert(0,dict(category='tax',date=today.isoformat(),title=f'세무 일일 브리핑 | {today.isoformat()}',
                        summary=summary[:280],url=relative))
    posts.sort(key=lambda x:x.get('date',''),reverse=True)
    posts_file.write_text(json.dumps(posts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'tax_seen.json').write_text(json.dumps(sorted(get_existing().union(ids)),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'CREATED {relative} with {len(items)} newly verified items',flush=True)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check',action='store_true');args=ap.parse_args()
    today=dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    end=today-dt.timedelta(days=1)
    last=[]
    for p in (ROOT/'briefings').glob('tax-????-??-??.html'):
        try:last.append(dt.date.fromisoformat(p.stem[4:]))
        except ValueError:pass
    start=max(last) if last else end
    # Backfill for weekends, but never silently process huge unreviewed periods.
    if (end-start).days>7:start=end-dt.timedelta(days=7)
    if args.check:
        ok=0
        for label,url in FEEDS.items():
            try:
                n,valid=rss_rows(label,url)
                print(f'RSS_OK {label}: entries={n}, valid_dated_links={len(valid)}',flush=True)
                ok+=1
            except Exception as exc:
                print(f'RSS_FAILED {label}: {type(exc).__name__}: {str(exc)[:120]}',flush=True)
        print(f'CHECK_SUMMARY: RSS sources reachable {ok}/{len(FEEDS)}. No OpenAI charge, no publication.',flush=True)
        if not ok:raise RuntimeError('No official RSS feed was reachable')
        return
    if start>end:
        print('NO_NEW_WINDOW: nothing due to collect',flush=True);return
    if (ROOT/f'briefings/tax-{today.isoformat()}.html').exists():
        print('ALREADY_PUBLISHED: a tax briefing exists for today',flush=True);return
    collected=[];successful=0
    for label,url in FEEDS.items():
        try:
            n,rows=rss_rows(label,url)
            successful+=1
            selected=[x for x in rows if start.isoformat()<=x['date']<=end.isoformat()
                      and any(term in (x['title']+' '+x['excerpt']) for term in TAX_TERMS)]
            collected+=selected
            print(f'RSS {label}: {n} entries, {len(selected)} tax-related in date window',flush=True)
        except Exception as exc:
            print(f'RSS_WARNING {label}: {type(exc).__name__}: {str(exc)[:110]}',flush=True)
    if successful==0:raise RuntimeError('No official RSS feed was available; no report created')
    # Web-search is a complement, not an exhaustive substitute for official structured sources.
    try:
        collected+=search_nts(start.isoformat(),end.isoformat())
    except Exception as exc:
        print(f'NTS_SEARCH_WARNING: {type(exc).__name__}: {str(exc)[:100]}',flush=True)
    unique={x['id']:x for x in collected}
    seen=get_existing()
    new=[x for k,x in unique.items() if k not in seen]
    print(f'TAX_COLLECT: {len(unique)} distinct official candidates, {len(new)} new',flush=True)
    if not new:
        print('NO_NEW_ITEMS: no verified new tax source; no new report',flush=True);return
    if len(new)>MAX_NEW:
        raise RuntimeError(f'{len(new)} new materials exceed limit {MAX_NEW}; review before publishing')
    summary,items=analyze(new)
    publish(today,start,end,summary,items,[x['id'] for x in new])

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('ERROR:',type(exc).__name__,str(exc)[:350],file=sys.stderr)
        sys.exit(1)
