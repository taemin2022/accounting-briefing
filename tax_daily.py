#!/usr/bin/env python3
"""Tax daily briefing using Korea Law Open Data (OC), standard library only.

CHECK MODE: python tax_daily.py --check   (read-only; does not call OpenAI or publish)
PUBLISH:    python tax_daily.py            (collect yesterday through unreported days)
"""
import argparse
import datetime as dt
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
SEARCH = 'https://www.law.go.kr/DRF/lawSearch.do'
OPENAI = 'https://api.openai.com/v1/responses'
TAX_LAWS = ('법인세', '부가가치세', '조세특례', '국세기본', '국세징수', '소득세', '국제조세',
            '상속세', '증여세', '지방세', '원천징수', '조세', '세무', '세액', '세금', '관세')
MAX_PER_REPORT = 24


def plain(text):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', str(text or '')))).strip()


def normalized_date(value):
    m = re.search(r'(20\d{2})\D?(\d{2})\D?(\d{2})', str(value or ''))
    if not m:
        return ''
    try:
        return dt.date(*map(int, m.groups())).isoformat()
    except ValueError:
        return ''


def fetch_xml(target, extra):
    oc = os.environ.get('LAW_API_OC', '').strip()
    if not oc:
        raise RuntimeError('LAW_API_OC secret is missing')
    params = dict(OC=oc, target=target, type='XML', display=100, page=1, **extra)
    print(f'Law API request: target={target}, parameter_names={sorted(params.keys())}, page=1 (OC hidden)', flush=True)
    req = urllib.request.Request(SEARCH + '?' + urllib.parse.urlencode(params), headers={
        'User-Agent': 'AccountingBriefing/1.0', 'Accept': 'application/xml, text/xml'})
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            raw = resp.read(3_000_001)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'Law API HTTP {e.code}; check OC and approved data categories') from None
    except urllib.error.URLError:
        raise RuntimeError('Law API connection error; retry later') from None
    if len(raw) > 3_000_000:
        raise RuntimeError('Law API response too large')
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise RuntimeError('Law API response was not valid XML; verify the OC and API settings') from None
    # The Law Open Data gateway also returns <Response><result>...</result><msg>...</msg></Response>
    # on request or authorization failures. Never mistake this for an empty document feed.
    if _tag(root).lower() == 'response' and root.find('msg') is not None:
        result = plain(root.findtext('result'))
        message = plain(root.findtext('msg'))
        oc = os.environ.get('LAW_API_OC', '').strip()
        # Protect OC and URLs if the gateway echoes request data. Do not dump raw XML.
        if oc:
            message = message.replace(oc, '[REDACTED]')
            result = result.replace(oc, '[REDACTED]')
        message = re.sub(r'https?://\S+', '[URL]', message)
        message = re.sub(r'(?i)(OC|authKey|apiKey)\s*[=:]\s*\S+', r'\1=[REDACTED]', message)
        message = message[:220]
        raise RuntimeError(f'Law API {target} gateway response: result={result[:30]!r}, msg={message!r}. Check OC registration, registered domain/IP, and request parameter names. The OC value itself is never logged.')
    txt = ' '.join((root.findtext(t) or '') for t in ('resultMsg', 'message', 'error', 'errorMessage'))
    err = (root.findtext('resultCode') or '').strip()
    if err and err not in ('0', '00', '000', '1', 'SUCCESS', 'success'):
        raise RuntimeError(f'Law API returned error code {err} for {target}')
    if any(word in txt.lower() for word in ('인증오류','사용할 수 없','승인되지','invalid oc')):
        raise RuntimeError(f'Law API returned an authorization error for {target}')
    return root


def _tag(node):
    return str(node.tag).split('}')[-1]


def _root_diagnostics(root):
    """Provide a safe schema summary without printing OC or source URLs."""
    children = [_tag(c) for c in list(root)[:12]]
    code = next((plain(''.join(c.itertext())) for c in root.iter()
                 if _tag(c).lower() in ('resultcode', 'errorcode', 'code')), '')
    msg = next((plain(''.join(c.itertext())) for c in root.iter()
                if _tag(c).lower() in ('resultmsg', 'errormessage', 'message', 'error', 'errmsg')), '')
    # Trim free-form messages: some upstream services echo request parameters.
    # Do not include message content in logs; only indicate whether a message exists.
    return f'root={_tag(root)!r}, child_tags={children}, code={code[:30]!r}, has_message={bool(msg)}'


def read_rows(root, required_tags, target='unknown'):
    """Accept well-formed empty results and reject unrecognized/failed responses."""
    found = []
    for node in root.iter():
        tags = {_tag(ch) for ch in node}
        if tags.intersection(required_tags) and len(tags) >= 2 and node is not root:
            found.append(node)
    # A service can return record fields directly at the root for a single result.
    if not found and {_tag(ch) for ch in root}.intersection(required_tags):
        found = [root]

    counts = [plain(''.join(el.itertext())) for el in root.iter()
              if _tag(el).lower() in ('totalcnt', 'totalcount', 'total_cnt')]
    counts = [c for c in counts if c.isdigit()]
    if not found:
        if counts and all(int(c) == 0 for c in counts):
            print(f'{target}: zero-result response confirmed by totalCnt=0', flush=True)
            return []
        # No results without a count can mean an authorization or query error.
        raise RuntimeError('Law API returned unrecognized XML; '
                           + _root_diagnostics(root) + '. Confirm API category authorization and request parameters.')
    return found


def fields(node):
    return {ch.tag.split('}')[-1]: plain(''.join(ch.itertext())) for ch in node}


def origin_url(raw, fallback):
    url = html.unescape(str(raw or '')).strip()
    if url.startswith('/'):
        url = 'https://www.law.go.kr' + url
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    if not url:
        url = fallback
    p = urllib.parse.urlsplit(url)
    if p.scheme != 'https' or p.hostname not in ('www.law.go.kr', 'law.go.kr', 'taxlaw.nts.go.kr') or p.username or p.password:
        return fallback
    return url


def collect_one_day(day):
    ymd = day.strftime('%Y%m%d')
    candidates = []
    # Official per-day amendment change feed, filtered to tax-law titles.
    source_specs = [
        ('lsHstInf', {'regDt': ymd}, {'법령명한글'}, '법령 변경', ('법령명한글',), ('등록일자','법령변경일','공포일자')),
        ('ntsCgmExpc', {'explYd': ymd+'~'+ymd}, {'안건명'}, '국세청 법령해석', ('안건명',), ('해석일자',)),
        ('ttSpecialDecc', {'rslYd': ymd+'~'+ymd}, {'사건명'}, '조세심판례', ('사건명',), ('의결일자',)),
    ]
    for target, args, required, source, title_keys, date_keys in source_specs:
        root = fetch_xml(target, args)
        rows = read_rows(root, required, target)
        total_raw = (root.findtext('totalCnt') or '').strip()
        if total_raw.isdigit() and int(total_raw) > len(rows):
            raise RuntimeError(f'{target} has {total_raw} results, only {len(rows)} returned. Refusing incomplete report.')
        print(f'{ymd}: {source}: {len(rows)} API records', flush=True)
        for row in rows:
            data = fields(row)
            title = next((data.get(k,'') for k in title_keys if data.get(k)), '')
            date = day.isoformat() if target == 'lsHstInf' else next((normalized_date(data.get(k)) for k in date_keys if normalized_date(data.get(k))), '')
            if not title or not date or date != day.isoformat():
                continue
            if source == '법령 변경' and not any(w in title for w in TAX_LAWS):
                continue
            key = next((data.get(k) for k in ('법령일련번호','법령해석일련번호','특별행정심판재결례일련번호','청구번호','안건번호') if data.get(k)), '')
            if not key:
                raise RuntimeError(f'{target}: official record identifier missing; cannot safely deduplicate')
            link = next((data.get(k) for k in ('법령상세링크','법령해석상세링크','행정심판재결례상세링크') if data.get(k)), '')
            # Official search link is safe when the record lacks a detail link.
            safe_query = urllib.parse.urlencode({'target':target,'type':'XML','query':title})
            fallback = f'https://www.law.go.kr/DRF/lawSearch.do?{safe_query}'
            url = origin_url(link, fallback)
            excerpt = '; '.join(f'{k}: {data[k]}' for k in ('제개정구분명','공포일자','시행일자','소관부처명','안건번호','청구번호','재결구분명') if data.get(k))
            candidates.append(dict(id=f'{target}:{key}:{date}', title=title, date=date, url=url,
                                   source=source, excerpt=excerpt[:1200]))
    return candidates


def prior_ids():
    path = ROOT/'tax_seen.json'
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data,list) or not all(isinstance(v,str) for v in data):
        raise RuntimeError('tax_seen.json is invalid; refusing to overwrite')
    return set(data)


def analyze(records):
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key:
        raise RuntimeError('OPENAI_API_KEY secret is missing')
    prompt = ('아래 법제처 OPEN API 기록에 대해 사실 확인 가능한 내용만으로 한국어 존댓말의 세무 실무 브리핑을 작성하십시오. '
        '국세청 법령해석은 본문이 제공되지 않으므로 제목과 메타데이터만으로 결론을 추론하지 마십시오. '
        '법령 변경 내용도 본문 전문이 없으면 개정 세부사항, 시행일, 세율, 실무 결론을 지어내지 마십시오. '
        '중요도는 기업의 법인세, 부가가치세, 조세특례 관련성을 중심으로 설명하십시오. '
        '반드시 각 id에 해당하는 summary 2~4문장과 implication 2~3문장을 작성하되, 확인할 수 없는 세부사항은 원문 확인 필요라고 명시하십시오. '
        'JSON만 반환하십시오: {"summary":"전체 안내", "items":[{"id":"...", "summary":"...", "implication":"..."}]}\n'
        + json.dumps(records, ensure_ascii=False))
    req = urllib.request.Request(OPENAI, data=json.dumps({'model':os.environ.get('OPENAI_MODEL','gpt-6.1-sol'),
        'input':prompt, 'max_output_tokens':10000},ensure_ascii=False).encode(),
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=220) as resp:
            payload=json.load(resp)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'OpenAI HTTP {e.code} during tax briefing') from None
    text='\n'.join(c.get('text','') for m in payload.get('output',[]) if m.get('type')=='message'
                  for c in m.get('content',[]) if c.get('type')=='output_text').strip()
    text=re.sub(r'^```(?:json)?\s*|\s*```$','',text,flags=re.I).strip()
    try: parsed=json.loads(text)
    except json.JSONDecodeError: raise RuntimeError('AI returned malformed JSON; publication skipped') from None
    mapped={x.get('id'):x for x in parsed.get('items',[]) if isinstance(x,dict)}
    if set(mapped) != {r['id'] for r in records}:
        raise RuntimeError('AI omitted or altered a document ID; publication skipped')
    items=[]
    for r in records:
        x=mapped[r['id']]
        if not str(x.get('summary','')).strip() or not str(x.get('implication','')).strip():
            raise RuntimeError('AI response omitted summary or implication')
        items.append(dict(title=r['title'],date=r['date'],url=r['url'],
                          summary=x['summary'],implication=x['implication']))
    return str(parsed.get('summary','')).strip(),items


def publish(today,start,end,summary,items,new_ids):
    from briefing import page, CATEGORIES
    # page() supports existing kinds; temporarily supply the fourth label without altering briefing.py.
    CATEGORIES['tax']='세무 일일 브리핑'
    title=f'세무 일일 브리핑 | {today.isoformat()}'
    url=f'briefings/tax-{today.isoformat()}.html'
    dst=ROOT/url
    if dst.exists():
        raise RuntimeError(f'{url} already exists; refusing overwrite')
    posts_file=ROOT/'posts.json'
    posts=json.loads(posts_file.read_text(encoding='utf-8')) if posts_file.exists() else []
    if not isinstance(posts,list): raise RuntimeError('posts.json is not an array')
    seen=prior_ids()
    dst.parent.mkdir(parents=True,exist_ok=True)
    output=page('tax',title,today.isoformat(),start.isoformat(),end.isoformat(),summary,items)
    dst.write_text(output,encoding='utf-8')
    posts.insert(0,dict(category='tax',date=today.isoformat(),title=title,summary=summary[:280],url=url))
    posts.sort(key=lambda p:p.get('date',''),reverse=True)
    posts_file.write_text(json.dumps(posts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'tax_seen.json').write_text(json.dumps(sorted(seen.union(new_ids)),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Created {url} with {len(items)} verified metadata records.',flush=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--check',action='store_true',help='Read-only API check, no OpenAI or publication')
    args=parser.parse_args()
    today=dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    end=today-dt.timedelta(days=1)
    # Avoid publishing historical 7-day collections on the first run.
    earlier=[]
    for p in (ROOT/'briefings').glob('tax-????-??-??.html'):
        try:earlier.append(dt.date.fromisoformat(p.stem[4:]))
        except ValueError:pass
    start=max(earlier) if earlier else end
    if args.check:
        # FIRST check the simplest API category that is explicitly selected on
        # the application (현행법령 목록 XML). Separate a shared authentication/
        # gateway problem from target-specific API failures.
        baseline = ('law', {'query': '법인세법'}, {'법령명한글'})
        ymd=end.strftime('%Y%m%d')
        probes=[('lsHstInf', {'regDt':ymd}, {'법령명한글'}),
                ('ntsCgmExpc', {'explYd':ymd+'~'+ymd}, {'안건명'}),
                ('ttSpecialDecc', {'rslYd':ymd+'~'+ymd}, {'사건명'})]
        failures=[]
        baseline_ok=False
        name, params, required=baseline
        try:
            root=fetch_xml(name,params)
            rows=read_rows(root,required,name)
            baseline_ok=True
            print(f'BASIC_CHECK_OK target=law: {len(rows)} matching rows, response_root={_tag(root)!r}',flush=True)
        except Exception as exc:
            print(f'BASIC_CHECK_FAILED target=law: {exc}',flush=True)
            print('DIAGNOSIS: The basic 현행법령 API also failed. Verify the registered OC and the approved access domain/IP, or consult the Law Open Data helpdesk with the sanitized gateway message.',flush=True)
        for target, params, required in probes:
            try:
                root=fetch_xml(target,params)
                rows=read_rows(root,required,target)
                print(f'CHECK_OK {target}: {len(rows)} returned rows (page 1)',flush=True)
            except Exception as exc:
                failures.append(target)
                print(f'CHECK_FAILED {target}: {exc}',flush=True)
        print(f'CHECK_SUMMARY: basic={"OK" if baseline_ok else "FAILED"}; specialized={len(probes)-len(failures)}/{len(probes)}. No files modified; no OpenAI charge.',flush=True)
        if not baseline_ok or failures:
            raise RuntimeError('API check incomplete; see BASIC_CHECK and CHECK_FAILED messages above')
        return
    if start>end:
        print('NO_NEW_WINDOW: no complete dates to collect.',flush=True)
        return
    if (end-start).days>14:
        raise RuntimeError('More than 14 unprocessed days; manual review needed')
    seen=prior_ids()
    records=[]
    for n in range((end-start).days+1):
        day=start+dt.timedelta(days=n)
        records.extend(collect_one_day(day))
    unique={r['id']:r for r in records}
    new=[r for i,r in unique.items() if i not in seen]
    print(f'Law API: {len(records)} candidates; {len(new)} unseen after ID deduplication.',flush=True)
    if not new:
        print('NO_NEW_ITEMS: no new tax materials found; no report created.',flush=True)
        return
    if len(new)>MAX_PER_REPORT:
        raise RuntimeError(f'{len(new)} unseen records exceed safe limit {MAX_PER_REPORT}; review manually')
    summary, items=analyze(new)
    if not summary:
        raise RuntimeError('AI summary was empty')
    publish(today,start,end,summary,items,[r['id'] for r in new])

if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        sys.exit(1)
