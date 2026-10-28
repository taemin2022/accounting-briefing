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
# Broad terms such as 신고, 공제, 관세 can occur in unrelated promotions.
# Conservative business-tax source filter. Promotional or general news is excluded
# even when the article casually mentions a tax-related word.
TAX_SUBJECTS = (
    '법인세', '부가가치세', '부가세', '원천징수', '원천세', '조세특례', '조특법',
    '세법', '세제개편', '세무조정', '세액공제', '세금계산서', '매입세액',
    '손금', '익금', '과세표준', '이전가격', '국제조세', '국세기본법',
    '조세심판', '유권해석', '법령해석', '세무조사', '과세처분',
    '관세법', '관세율', '관세평가', '관세환급', '관세 환급', '수입부가세',
    '납세의무', '세액감면', '국세청 사전답변', '조세조약',
)
SUBSTANTIVE_TOPICS = (
    '개정', '시행', '입법예고', '행정예고', '법률안', '시행령', '시행규칙',
    '해석', '예규', '사전답변', '질의회신', '판례', '판결', '심판', '결정',
    '신고기한', '납부기한', '공제', '감면', '과세', '세액', '세율',
    '세무조사', '부과', '환급', '손금', '익금', '원천징수', '세무조정',
    '세금계산서', '세제개편', '징수', '조세특례',
)
PROMOTIONAL_OR_ADMIN = (
    '할인', '페스티벌', '축제', '이벤트', '캠페인', '경품', '기념식',
    '모집', '채용', '설명회', '전시회', '박람회', '홍보', '서포터즈',
    '현장 점검', '현장점검', '상담창구', '민원상담', '업무협약',
    '인사발령', '인사 발령', '장관 방문', '간담회', '소비 진작',
    '소비촉진', '가격 인하', '가격할인', '고객지원', '체험행사',
    '수상', '우수기관', '행사 개최', '지자체 축제',
)

def relevant_title(record):
    title=clean(record.get('title', '')).lower()
    source=clean(record.get('source', '')).lower()
    if not title: return False
    if any(word.lower() in title for word in PROMOTIONAL_OR_ADMIN):
        return False
    # Never use the body excerpt alone to infer relevance from incidental tax words.
    if not any(word.lower() in title for word in TAX_SUBJECTS):
        return False
    # Stand-alone law rulings can be substantively relevant without a '개정' verb.
    if '세법해석사례' in source or '국세법령정보' in source:
        return True
    return any(word.lower() in title for word in SUBSTANTIVE_TOPICS)

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
    prompt='''당신은 제조업 법인의 세무 실무자료 편집자입니다. 아래 후보들을 매우 보수적으로 선별하십시오.
'기업의 세법상 의무 또는 권리, 신고·납부, 세무조정, 세액공제/감면, 과세 및 조세심판 판단에 구체적으로 영향을 주는 공식 신규 발표'만 include=true입니다.
선정 기준: (1) 법인세·부가가치세·원천세·국제조세·관세의 실질적인 법령/시행령/시행규칙 개정 또는 입법예고,
(2) 적용 기업에 직접적인 효력이 있는 국세청·관세청 예규/유권해석/심판·판결,
(3) 기업 신고·납부기한/의무/세율/공제/감면의 실제 변경에 한합니다.
제외: 행사·축제·쿠폰·할인·정책홍보, 현장점검, 해외직구 상담, 인사·채용, 캠페인, 행정기관 내부 활동, 일반 소비자 보호,
단순 경제·산업동향, '세금' 언급만 포함된 기사, 기업 세무에 구체적인 변동이 없는 단순 안내.
중요: 기사 제목에 세무 용어가 있다는 이유만으로 선정하지 마십시오. 직접적인 세법·신고·해석 변경이 문서 발췌에 나타나야 합니다.
근거 자료가 부족해 실무 영향 여부가 불명확하면 include=false로 하십시오. 실제 발표를 확인하지 못한 세율·시행일·해석 결론을 만들어내지 마십시오.
선정된 기사만 한국어 존댓말로 정확한 사실 요약 2~4문장, 기업 실무 영향 2~3문장을 쓰되 확인 불가 항목은 원문 확인 필요라고 적으십시오.
선정 건수가 0이어도 괜찮습니다. JSON 형식 외의 문구를 출력하지 마십시오.
JSON 형식: {"summary":"선정된 기사에 대한 요약(없으면 빈 문자열)", "items":[{"id":"...", "include":true, "summary":"...", "implication":"..."}]}
자료:\n'''+json.dumps(records,ensure_ascii=False)
    result=ask_ai(prompt)
    mapped={r['id']:r for r in result.get('items',[]) if isinstance(r,dict) and isinstance(r.get('id'),str)}
    if set(mapped)!={r['id'] for r in records}:raise RuntimeError('AI output IDs did not match candidates; report not published')
    items=[]
    for r in records:
        x=mapped[r['id']]
        if x.get('include') is not True:
            continue
        if not clean(x.get('summary','')) or not clean(x.get('implication','')):
            raise RuntimeError('AI left empty analysis text')
        items.append(dict(title=r['title'],date=r['date'],url=r['url'],summary=clean(x['summary']),implication=clean(x['implication'])))
    overall=clean(result.get('summary',''))
    if items and not overall:raise RuntimeError('AI returned empty overall summary')
    print(f'TAX_RELEVANCE: {len(items)}/{len(records)} candidates retained',flush=True)
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
    suppressed_file=ROOT/'tax_excluded_reports.json'
    if suppressed_file.exists() and not args.check:
        suppressed=json.loads(suppressed_file.read_text(encoding='utf-8'))
        if not isinstance(suppressed,list) or not all(isinstance(v,str) for v in suppressed):
            raise RuntimeError('tax_excluded_reports.json must be a list of date strings')
        if today.isoformat() in suppressed:
            print('SUPPRESSED_REPORT: this report date was intentionally removed. No republishing.',flush=True)
            return
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
                      and relevant_title(x)]
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
    before=len(new)
    new=[r for r in new if relevant_title(r)]
    print(f'TAX_COLLECT: {len(unique)} distinct candidates, {before} new, {len(new)} relevant titles',flush=True)
    if not new:
        print('NO_NEW_ITEMS: no verified new tax source; no new report',flush=True);return
    if len(new)>MAX_NEW:
        raise RuntimeError(f'{len(new)} new materials exceed limit {MAX_NEW}; review before publishing')
    summary,items=analyze(new)
    if not items:
        print('NO_RELEVANT_ITEMS: no business-tax material approved for publication',flush=True)
        return
    publish(today,start,end,summary,items,[x['id'] for x in new if x['title'] in {i['title'] for i in items}])

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('ERROR:',type(exc).__name__,str(exc)[:350],file=sys.stderr)
        sys.exit(1)
