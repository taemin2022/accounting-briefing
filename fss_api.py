#!/usr/bin/env python3
"""FSS official press release API -> OpenAI analysis -> existing briefing site.

Secrets required: FSS_API_KEY and OPENAI_API_KEY. Python 3.12, standard library only.
"""
import datetime as dt
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import urllib.error
from zoneinfo import ZoneInfo

from briefing import ROOT, page, formalize, display_text

FSS_ENDPOINT = 'https://www.fss.or.kr/fss/kr/openApi/api/bodoInfo.jsp'
OPENAI_ENDPOINT = 'https://api.openai.com/v1/responses'

class StripHTML(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def text(self):
        return re.sub(r'\s+', ' ', html.unescape(' '.join(self.parts))).strip()

def plain(value):
    parser = StripHTML(); parser.feed(str(value or '')); return parser.text()

def normalize_date(value):
    m = re.search(r'(20\d{2})\D?(\d{2})\D?(\d{2})', str(value))
    if not m: return ''
    try: return dt.date(int(m[1]), int(m[2]), int(m[3])).isoformat()
    except ValueError: return ''

def valid_official_link(value):
    try:
        url = str(value or '').strip().replace('&amp;', '&')
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ('http','https') or p.username or p.password or p.port not in (None,80,443): return ''
        if (p.hostname or '').lower() not in ('fss.or.kr','www.fss.or.kr'): return ''
        # Always display HTTPS for the official origin page.
        return urllib.parse.urlunsplit(('https',p.netloc,p.path,p.query,''))
    except ValueError: return ''

def fetch_fss(start, end):
    key = os.environ.get('FSS_API_KEY', '').strip()
    if not key: raise RuntimeError('FSS_API_KEY secret is missing')
    params = urllib.parse.urlencode({'apiType':'json', 'startDate':start, 'endDate':end, 'authKey':key})
    req = urllib.request.Request(FSS_ENDPOINT + '?' + params, headers={'User-Agent':'AccountingBriefing/1.0','Accept':'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read(8_000_001)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'FSS API returned HTTP {exc.code} (check key/approval)') from None
    except urllib.error.URLError as exc:
        raise RuntimeError('FSS API connection failed; please retry') from None
    if len(raw) > 8_000_000: raise RuntimeError('FSS API payload unexpectedly large')
    try:
        payload=json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError):
        raise RuntimeError('FSS API response is not valid JSON') from None
    if not isinstance(payload,dict): raise RuntimeError('FSS API returned unexpected JSON structure')
    code = str(payload.get('resultCode','')).strip()
    if code and code not in ('00','000','0','200','SUCCESS','success'):
        # Never print payload: it could include request metadata or secrets.
        raise RuntimeError(f'FSS API error code {code}; verify approval and request parameters')
    records = payload.get('result', [])
    if isinstance(records,dict): records=[records] if records else []
    if not isinstance(records,list): raise RuntimeError('FSS API result is not an array')
    try: count=int(payload.get('resultCnt',len(records)))
    except (ValueError,TypeError): count=len(records)
    if count>len(records):
        raise RuntimeError(f'FSS API returned {len(records)} of {count} records; refusing partial publication')
    print(f'FSS API: {len(records)} records returned for {start}~{end}.', flush=True)
    result=[]; seen=set()
    for entry in records:
        if not isinstance(entry,dict):continue
        url=valid_official_link(entry.get('originUrl'))
        date=normalize_date(entry.get('regDate'))
        title=plain(entry.get('subject'))
        ident=str(entry.get('contentId','')).strip()
        if not (title and start<=date<=end and url):
            print('Skipped FSS item with invalid date/title/origin URL.',flush=True)
            continue
        if url in seen: continue
        seen.add(url)
        result.append({'id':ident or url, 'title':title,'date':date,'url':url,
                       'body':plain(entry.get('contentsKor'))[:4500],
                       'publisher':plain(entry.get('publishOrg'))[:120]})
    return result

def prior_urls():
    urls=set()
    for path in (ROOT/'briefings').glob('fss-*.html'):
        try:
            content=path.read_text(encoding='utf-8')
            urls.update(html.unescape(url) for url in re.findall(r'href="(https?://[^\"]+)"',content))
        except OSError:pass
    return urls

def analyze(records):
    key=os.environ.get('OPENAI_API_KEY','').strip()
    if not key: raise RuntimeError('OPENAI_API_KEY secret is missing')
    if len(records)>30:
        raise RuntimeError('Over 30 new FSS reports in catch-up window; review rather than truncate silently')
    snippets=[{'id':str(i),'title':r['title'],'date':r['date'],'publisher':r['publisher'],
               'contents':r['body'] or '(API에 본문이 제공되지 않았습니다. 제목만으로 세부 내용을 추론하지 마십시오.)'}
              for i,r in enumerate(records)]
    prompt=(
       '다음은 금융감독원 공식 보도자료 API에서 직접 조회한 발표 목록입니다. 제공된 문서에서 확인되는 정보만 요약하십시오. '
       '각 id에 대해 빠짐없이 한 건씩 작성하십시오. 보도자료 제목은 별도로 입력되므로 새로 만들어내지 마십시오. '
       '우선 회계·외부감사·공시·내부회계관리제도 연관성을 분석하되 다른 보도자료도 핵심 내용을 기술하십시오. '
       '기사 본문이 없는 경우 그 사실과 분석의 제한을 명확히 쓰고 사실을 추정하거나 창작하지 마십시오. '
       'summary는 중요한 사실 3~5문장, implication은 회계·감사·내부통제 및 업계 관련 실무 영향과 확인할 사항을 2~4문장으로 작성하십시오. '
       '회계와 무관하면 무관하다고 명시하고 과장된 영향을 추정하지 마십시오. '
       '반드시 정중한 한국어 종결형(~습니다/~합니다)을 사용하십시오. '
       'JSON만 반환: {"summary":"전체 요약 2~3문장", "items":[{"id":"0","summary":"...","implication":"..."}]}\n'
       +json.dumps(snippets,ensure_ascii=False)
    )
    data={'model':os.environ.get('OPENAI_MODEL','gpt-6.1-sol'),'input':prompt,'max_output_tokens':12000}
    req=urllib.request.Request(OPENAI_ENDPOINT, data=json.dumps(data,ensure_ascii=False).encode('utf-8'),
               headers={'Authorization':f'Bearer {key}','Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=240) as resp: payload=json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'OpenAI API failed HTTP {exc.code}; check model access or balance') from None
    output='\n'.join(c.get('text','') for m in payload.get('output',[]) if m.get('type')=='message'
                   for c in m.get('content',[]) if c.get('type')=='output_text').strip()
    output=re.sub(r'^```(?:json)?\s*|\s*```$','',output,flags=re.I).strip()
    try: result=json.loads(output)
    except json.JSONDecodeError:raise RuntimeError('OpenAI analysis was not valid JSON; nothing published') from None
    if not isinstance(result,dict) or not isinstance(result.get('items'),list):
        raise RuntimeError('OpenAI analysis has unexpected structure')
    mapped={str(item.get('id')):item for item in result['items'] if isinstance(item,dict)}
    if len(mapped)!=len(records) or any(str(i) not in mapped for i in range(len(records))):
        raise RuntimeError('AI analysis skipped or duplicated FSS reports; publication stopped')
    final=[]
    for i,source in enumerate(records):
        item=mapped[str(i)]; summ=str(item.get('summary','')).strip(); imp=str(item.get('implication','')).strip()
        if not summ or not imp:raise RuntimeError('AI analysis contains blank report detail')
        final.append({'title':source['title'],'date':source['date'],'url':source['url'],
                      'summary':summ,'implication':imp})
    return str(result.get('summary','')).strip(), final

def publish(now,start,end,summary,items):
    title=f'금융감독원 일일 브리핑 | {now.isoformat()}'
    url=f'briefings/fss-{now.isoformat()}.html'
    path=ROOT/url
    listpath=ROOT/'posts.json'
    try:
        posts=json.loads(listpath.read_text(encoding='utf-8'))
        if not isinstance(posts,list):raise ValueError('not array')
    except FileNotFoundError:posts=[]
    except (ValueError,json.JSONDecodeError):raise RuntimeError('Invalid posts.json; refusing overwrite') from None
    # Don't overwrite a report from the same day with fewer source items.
    if path.exists():
        raise RuntimeError(f'{url} already exists; refusing to overwrite daily release. Rerun tomorrow or review manually.')
    path.parent.mkdir(parents=True,exist_ok=True)
    title_summary=formalize(summary)[:280]
    rendered=page('fss',title,now.isoformat(),start,end,title_summary,items)
    path.write_text(rendered,encoding='utf-8')
    posts=[p for p in posts if isinstance(p,dict) and p.get('url')!=url]
    posts.append({'category':'fss','date':now.isoformat(),'title':title,
                  'summary':title_summary,'url':url})
    posts.sort(key=lambda p:p.get('date',''),reverse=True)
    listpath.write_text(json.dumps(posts,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f'Created {url}: {len(items)} official FSS announcements.',flush=True)

def main():
    today=dt.datetime.now(ZoneInfo('Asia/Seoul')).date()
    end=today-dt.timedelta(days=1)
    start=end-dt.timedelta(days=6) # lookback prevents missed records; existing reports are deduplicated
    found=fetch_fss(start.isoformat(),end.isoformat())
    already=prior_urls()
    new=[r for r in found if r['url'] not in already]
    print(f'Deduplication: {len(found)-len(new)} previously published, {len(new)} new.',flush=True)
    if not new:
        print('NO_NEW_ITEMS: no new verified FSS releases. No report created.',flush=True)
        return
    summary,items=analyze(new)
    publish(today,start.isoformat(),end.isoformat(),summary,items)

if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Do not print full URLs, query strings, secrets or API payloads.
        print(f'ERROR: {exc}',file=sys.stderr)
        sys.exit(1)
