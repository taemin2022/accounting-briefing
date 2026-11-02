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
# Business tax relevance: broader subject coverage, but reject promotions.
# Title alone is not enough to confirm significance; AI reviews collected excerpts.
TAX_SUBJECTS = (
    '법인세', '부가가치세', '부가세', '원천징수', '원천세', '조세특례', '조특법',
    '세법', '세제', '세무', '세무조정', '세액공제', '세액감면', '세금계산서',
    '전자세금계산서', '매입세액', '손금', '익금', '과세표준', '이전가격',
    '국제조세', '국세기본법', '조세심판', '과세처분', '납세의무',
    '법령해석', '유권해석', '사전답변', '질의회신', '세무조사', '조세조약',
    '관세법', '관세율', '관세평가', '관세환급', '수입부가세', '통관',
    '원산지', 'FTA', '기업 세부담', '신고납부', '신고 안내', '납부기한',
    '신고기한', '부과제척기간', '가산세', '경정청구', '수정신고',
    '조세불복', '공제감면',
)
BUSINESS_CONTEXT = (
    '기업', '법인', '사업자', '수출', '수입', '사업장', '제조', '중소기업',
    '납세자', '거래', '외국법인', '국내법인', '국외', '신고', '납부',
    '세액', '공제', '세무', '과세', '가산세', '조세', '장부', '해석',
)
TAX_ACTIONS = (
    '개정', '시행', '입법예고', '행정예고', '법률안', '시행령', '시행규칙',
    '해석', '예규', '사전답변', '질의회신', '판례', '판결', '심판', '결정',
    '신고', '납부', '신청', '절차', '안내', '기한', '연장', '정정', '변경',
    '공제', '감면', '과세', '세액', '세율', '환급', '징수', '조사',
    '신고서', '세무조정', '세금계산서', '추징', '불복', '경정',
)
PROMOTIONAL_OR_ADMIN = (
    '페스티벌', '축제', '이벤트', '캠페인', '경품', '기념식',
    '채용', '서포터즈', '체험행사', '장관 방문', '소비촉진',
    '소비 진작', '가격 인하', '가격할인', '할인 행사', '할인행사',
    '고객지원센터', '해외직구 상담', '현장점검', '현장 점검',
    '상담창구', '민원상담', '업무협약', '인사발령', '인사 발령',
    '수상', '우수기관', '행사 개최', '관광', '특판',
)

def preliminary_candidate(record):
    """Only eliminate obviously irrelevant promotions before AI content review.

    A generic title is *not* evidence of irrelevance. Every official dated
    candidate that survives this minimal filter reaches the AI reviewer.
    """
    title = clean(record.get('title', '')).lower()
    if not title:
        return False
    # Promotions and unrelated institutional news are clear non-tax items.
    # If a title also identifies concrete business tax changes, retain it for
    # contextual review rather than risking a false negative.
    promotional = any(word.lower() in title for word in PROMOTIONAL_OR_ADMIN)
    substantive_tax = any(word.lower() in title for word in TAX_SUBJECTS)
    return not (promotional and not substantive_tax)


class _OfficialTextParser(__import__('html.parser', fromlist=['HTMLParser']).HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'noscript', 'svg'):
            self.skip_depth += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style', 'noscript', 'svg') and self.skip_depth:
            self.skip_depth -= 1
        if tag in ('p','div','li','br','tr','h1','h2','h3'):
            self.parts.append(' ')
    def handle_data(self, data):
        if self.skip_depth == 0:
            self.parts.append(data)


def enrich_official_excerpt(record):
    """Best-effort bounded official-page text; never replaces provenance rules."""
    excerpt = clean(record.get('excerpt', ''))
    if len(excerpt) >= 300:
        return record
    url = safe_url(record.get('url', ''))
    if not url:
        return record
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent':'AccountingBriefingRSS/1.0',
            'Accept':'text/html,application/xhtml+xml'})
        with urllib.request.urlopen(req, timeout=12) as response:
            if not safe_url(response.geturl()):
                return record
            content_type = response.headers.get('Content-Type','').lower()
            if 'html' not in content_type:
                return record
            raw = response.read(150_001)
        if len(raw) > 150_000:
            raw = raw[:150_000]
        # Most Korean official websites use UTF-8; decode with replacement when needed.
        document = raw.decode('utf-8', errors='replace')
        parser = _OfficialTextParser()
        parser.feed(document)
        full_text = clean(' '.join(parser.parts))
        if len(full_text) > len(excerpt):
            copy = dict(record)
            # Keep collected excerpt first; a webpage may include navigation.
            copy['excerpt'] = (excerpt + ' ' + full_text[:3000]).strip()[:3600]
            return copy
    except (urllib.error.URLError, TimeoutError, ValueError, UnicodeError, OSError):
        pass
    return record

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

# Official agency subdomains. Do not widen to news portals or unverified sites.
SEARCH_PROFILES = (
    ('국세청 보도·신고안내', ('nts.go.kr',),
     '국세청 보도자료·공지사항·법인세/부가가치세/원천세 신고·납부 안내·전자세금계산서·세무조사'),
    ('국세청 예규·해석', ('taxlaw.nts.go.kr', 'nts.go.kr'),
     '국세법령정보시스템 예규·질의회신·사전답변·법령해석·법인세·부가가치세·세무조정'),
    ('조세심판·판례', ('taxlaw.nts.go.kr', 'nts.go.kr'),
     '국세법령정보시스템 조세심판례·판례·결정례·법인세·부가가치세·국제조세'),
)

def official_host(url, allowed):
    host = (urllib.parse.urlsplit(url).hostname or '').lower()
    return any(host == name or host.endswith('.' + name) for name in allowed)

def search_nts_profile(label, domains, topics, start, end):
    domain_queries = ' '.join('site:' + d for d in domains)
    prompt = f'''한국시간 {start}부터 {end}까지 새로 게시된 공식 세무 자료를 찾으십시오.
수집 분야: {label}. 조사 주제: {topics}.
공식 검색 대상: {', '.join(domains)}. 검색어에 {domain_queries}를 활용하여
기관의 게시판 및 개별 원문을 직접 확인하십시오. 동일한 검색어만 반복하지 말고
주제별로 검색하십시오. 기사 작성일이나 검색 인덱스 시각이 아닌 기관 원문의
등록일·게시일이 {start}~{end} 범위에 들어오는 항목만 채택하십시오.
발표일을 알 수 없으면 제외하십시오. 과거 문서를 오늘 게시물로 추정하지 마십시오.
최대 12건을 제시하되 없으면 빈 배열로 응답하십시오. 제목·게시일·공식 원문 URL,
확인 가능한 짧은 내용만 작성하십시오. 제삼자 사이트의 재인용 및 추정 URL 금지.
JSON만 출력: {{"items":[{{"title":"...","date":"YYYY-MM-DD","url":"https://...","excerpt":"..."}}]}}'''
    result = ask_ai(prompt, search=True)
    candidates = result.get('items', [])
    if not isinstance(candidates,list):
        raise RuntimeError('Web search output items is not an array')
    rows=[]
    stat={'raw':len(candidates),'invalid_date':0,'invalid_domain':0,
          'unreachable':0,'invalid_content':0,'accepted':0}
    for x in candidates:
        if not isinstance(x,dict):continue
        url=safe_url(x.get('url',''))
        if not url or not official_host(url,domains):
            stat['invalid_domain']+=1;continue
        date=parse_date(x.get('date',''))
        if not date or not start<=date<=end:
            stat['invalid_date']+=1;continue
        title=clean(x.get('title',''))
        excerpt=clean(x.get('excerpt',''))
        if not title or not excerpt:
            stat['invalid_content']+=1;continue
        if not verify_official_link(url):
            stat['unreachable']+=1;continue
        rows.append(dict(id=hashlib.sha256(canonical(url).encode()).hexdigest(),
                         title=title,date=date,url=url,source=label,
                         excerpt=excerpt[:1200]))
    stat['accepted']=len(rows)
    print(f'TAX_WEB {label}: ' + ', '.join(f'{k}={v}' for k,v in stat.items()),flush=True)
    return rows

def search_nts(start,end):
    collected=[];success=0
    for label,domains,topics in SEARCH_PROFILES:
        try:
            collected.extend(search_nts_profile(label,domains,topics,start,end))
            success+=1
        except Exception as exc:
            print(f'TAX_WEB_WARNING {label}: {type(exc).__name__}: {str(exc)[:160]}',flush=True)
    print(f'TAX_WEB_SUMMARY: search_profiles={success}/{len(SEARCH_PROFILES)}, '
          f'verified_rows={len(collected)}, unique_urls={len({r["id"] for r in collected})}',flush=True)
    return collected

def _review_items(payload, expected_ids):
    '''Normalize common AI JSON shapes without assuming article identity.

    The model may return an empty items list when it considers nothing relevant,
    or return a single verdict at the top level instead of the requested list.
    Never treat a missing review as evidence of irrelevance.
    '''
    if not isinstance(payload, dict):
        return None
    outputs = payload.get('items')
    if outputs is None:
        outputs = payload.get('results', payload.get('decisions'))
    if isinstance(outputs, dict):
        outputs = [dict(v, id=k) for k, v in outputs.items() if isinstance(v, dict)]
    if isinstance(outputs, list):
        outputs = [x for x in outputs if isinstance(x, dict)]
        if len(outputs) == len(expected_ids):
            ids = [str(x.get('id', '')).strip() for x in outputs]
            if set(ids) == set(expected_ids) and len(set(ids)) == len(ids):
                return dict(zip(ids, outputs))
            # Only a single record can be unambiguously associated by position.
            if len(expected_ids) == 1:
                return {expected_ids[0]: outputs[0]}
        # Explicit empty array on a single-record review may indicate omission;
        # do not assume the candidate was rejected without a decision.
    if len(expected_ids) == 1 and 'include' in payload:
        return {expected_ids[0]: payload}
    return None


def analyze(records):
    '''Review all candidates, validating a verdict for each supplied ID.'''
    review_ids = {r['id']: f'T{idx:03d}' for idx, r in enumerate(records, 1)}
    review_records = [dict(r, id=review_ids[r['id']]) for r in records]
    prompt = ('''당신은 한국 제조업 법인의 세무 실무 담당자를 위한 편집자입니다.
입력한 공식자료 각 건을 반드시 검토하십시오. 출력에는 모든 입력 ID에 대한 결과가 있어야 합니다.
기업의 법인세·부가가치세·원천세·세무조정·국제조세·관세·신고절차 등에 적용되거나
공식 예규·해석·판례·조세심판례로서 실무 참고 가치가 있으면 include=true로 판단하십시오.
제도 개정이 없더라도 실무 참고가치가 있는 공식 안내는 포함할 수 있습니다.
단, 할인행사·축제·쿠폰·소비촉진·기관 홍보·단순 방문·해외직구 민원은 제외하십시오.
자료 제목과 본문/요약을 함께 판단하십시오. 제목에 세무 용어가 없다는 이유만으로 제외하지 마십시오.
원문이 확인되지 않은 금액·세율·시행일·법적 효과를 만들지 마십시오.
선정 건에만 summary(공식 사실 3~5문장)와 implication(기업 실무 점검사항 3~5문장)을
한국어 존댓말로 작성하십시오. 제외 건도 id와 include=false로 반드시 반환하십시오.
JSON 객체 형식 이외의 텍스트는 출력하지 마십시오.
형식: {"summary":"전체 요약(선정 건이 없으면 빈 문자열)","items":[{"id":"T001","include":false,"summary":"","implication":""}]}
입력 자료:\n''' + json.dumps(review_records, ensure_ascii=False))
    expected_ids = [review_ids[r['id']] for r in records]
    payload = ask_ai(prompt)
    mapped = _review_items(payload, expected_ids)
    if mapped is None:
        shape = list(payload)[:8] if isinstance(payload, dict) else [type(payload).__name__]
        count = len(payload.get('items', [])) if isinstance(payload, dict) and isinstance(payload.get('items'), list) else 'non-list'
        print(f'TAX_AI_FORMAT_RETRY: received_keys={shape}, items_count={count}, expected={len(records)}; retrying focused review', flush=True)
        retry_prompt = prompt + ('\n주의: 직전 응답은 입력 자료 수와 결과 수가 일치하지 않았습니다. '
                                 '입력 ID마다 판단이 필요합니다. 실무와 관계없어도 해당 ID를 include=false로 반환하십시오. '
                                 'items 배열을 절대 생략하거나 빈 배열로 반환하지 마십시오.')
        payload = ask_ai(retry_prompt)
        mapped = _review_items(payload, expected_ids)
    if mapped is None:
        raise RuntimeError('AI review JSON did not contain one verdict per candidate after retry; no report was published')
    print(f'TAX_AI_ID_CHECK: {len(mapped)}/{len(records)} candidate IDs matched', flush=True)
    items=[]
    for r in records:
        x = mapped[review_ids[r['id']]]
        include = x.get('include', False)
        if isinstance(include, str):
            include = include.strip().lower() in ('true', 'yes', '1', '포함')
        if include is not True:
            continue
        summary = clean(x.get('summary', ''))
        implication = clean(x.get('implication', ''))
        if not (summary and implication):
            raise RuntimeError('AI marked an item relevant but omitted summary or practical implication')
        items.append(dict(title=r['title'], date=r['date'], url=r['url'],
                          summary=summary, implication=implication))
    overall=clean(payload.get('summary', ''))
    if items and not overall:
        # Use the reviewed facts rather than fabricate another summary.
        overall=' '.join(item['summary'] for item in items)[:560]
    print(f'TAX_RELEVANCE: {len(items)}/{len(records)} candidates retained', flush=True)
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
    collected=[];successful=0;rss_total=0;rss_dated=0;rss_in_window=0;rss_filtered=0
    for label,url in FEEDS.items():
        try:
            n,rows=rss_rows(label,url)
            successful+=1
            dated=[x for x in rows if start.isoformat()<=x['date']<=end.isoformat()]
            selected=[x for x in dated if preliminary_candidate(x)]
            rss_total+=n;rss_dated+=len(rows);rss_in_window+=len(dated);rss_filtered+=len(selected)
            collected+=selected
            print(f'RSS {label}: entries={n}, valid_dated_links={len(rows)}, '
                  f'in_window={len(dated)}, promotion_passed={len(selected)}',flush=True)
        except Exception as exc:
            print(f'RSS_WARNING {label}: {type(exc).__name__}: {str(exc)[:110]}',flush=True)
    print(f'TAX_RSS_SUMMARY: sources_ok={successful}/{len(FEEDS)}, entries={rss_total}, '
          f'dated_links={rss_dated}, in_window={rss_in_window}, promotion_passed={rss_filtered}',flush=True)
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
    new=[r for r in new if preliminary_candidate(r)]
    print(f'TAX_COLLECT: {len(unique)} distinct candidates, {before} new, {len(new)} sent to content review',flush=True)
    if not new:
        if not unique:
            reason='SOURCE_EMPTY: no dated official tax candidates were confirmed by RSS or web search'
        elif not before:
            reason='ALREADY_SEEN: candidate official URLs were already published or tracked'
        else:
            reason='PROMOTION_FILTERED: only clearly unrelated promotion/administration items found'
        print(f'NO_NEW_ITEMS: {reason}; no new report',flush=True)
        return
    if len(new)>MAX_NEW:
        raise RuntimeError(f'{len(new)} new materials exceed limit {MAX_NEW}; review before publishing')
    new=[enrich_official_excerpt(r) for r in new]
    print(f'TAX_CONTENT_REVIEW: {len(new)} official candidates forwarded to AI, '+
          f'{sum(bool(clean(x.get("excerpt"))) for x in new)} with content/excerpts',flush=True)
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
