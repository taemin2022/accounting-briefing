#!/usr/bin/env python3
"""Accounting briefing prototype. Uses Responses API + web_search, no third-party Python packages.
Manual testing first; scheduled runs are enabled by editing the workflow later.
"""
import argparse
import datetime as dt
import html
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
import urllib.parse
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Seoul')
API = 'https://api.openai.com/v1/responses'
CATEGORIES = {'fss': '금융감독원 일일 브리핑', 'ifrs': 'K-IFRS·내부회계 일일 브리핑', 'security': '보안시장 주간 동향'}


def time_range(kind, now):
    if kind == 'ifrs':
        # Publish only prior-calendar-day announcements in each daily briefing.
        yesterday = (now.date() - dt.timedelta(days=1)).isoformat()
        return yesterday, yesterday
    if kind == 'fss':
        # Keep existing seven-day catch-up until official FSS API is connected.
        yesterday = now.date() - dt.timedelta(days=1)
        return (yesterday - dt.timedelta(days=6)).isoformat(), yesterday.isoformat()
    monday = now.date() - dt.timedelta(days=now.weekday() + 7)
    return monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()


def instructions(kind, start, end):
    prefix = f'한국시간 기준 검색대상기간: {start} ~ {end}. 이 기간에 공식 발표된 자료만 포함하고 더 오래된 발표는 넣지 마라. 원문의 실제 발표일을 확인하라. 금융감독원 브리핑은 기존대로 최근 7일을 검색하며 이미 게시된 원문은 중복 제외한다.\\n'
    if kind == 'fss':
        domain = '금융감독원 공식 보도자료를 우선 확인. 모든 보도자료 중 회계·감사·공시·감리·내부회계 관련 자료를 우선 배치하고, 그 외 중요한 금융감독원 발표도 누락하지 않도록 한다.'
    elif kind == 'ifrs':
        domain = ('검색을 하나의 광범위한 질의에 의존하지 말고 아래 공식 출처를 기관별로 별도 검색하라: '
                  '① 한국회계기준원 kasb.or.kr(회계기준 제·개정·공개초안·보도자료), '
                  '② 금융위원회 fsc.go.kr 및 금융감독원 fss.or.kr(회계·감리·공시·내부회계 감독), '
                  '③ IFRS Foundation ifrs.org(기준서 발표·IASB 프로젝트), '
                  '④ 한국공인회계사회 kicpa.or.kr(공식 감사·내부회계 발표). '
                  '각 기관별로 검색대상기간의 공지·보도자료가 실제 게시됐는지 확인하라. '
                  'K-IFRS 1118, 1115, 1002, 1116, 연결내부회계 및 ITGC 실무 관련 신규 발표를 우선 분석하라. '
                  '기존 기준서 설명이나 이전 발표를 어제 발표한 것으로 오인하지 마라. '
                  '기관 공식 발표일과 기준서 효력발생일을 엄격히 구분하고, 대상기간 발표일을 증명하지 못하면 포함하지 마라.')
    else:
        domain = '국내외 CCTV/IP카메라, VMS/NVR, AI 영상분석, VSaaS, 출입통제, 이미지센서, 입찰/RFP, 규제 및 경쟁사(Axis, Hanwha Vision, Hikvision, Dahua, i-PRO, Genetec, Milestone) 동향을 확인하고 제조사 관점의 시사점을 분석한다.'
    return (prefix + domain + '\n웹 검색을 사용해 실제 원문 링크를 찾아라. 출처가 없는 문서는 절대 생성하지 마라. 불확실한 내용은 제외하라. '
            '동일 사건 중복 제거. 대상기간에 근거 있는 자료가 없다면 items를 빈 배열로 반환하라. '
            '출처 웹 주소는 실제 원문 URL을 그대로 사용하라. 현재 운영 목적은 시험용 자동 브리핑이다. '
            '반드시 JSON 객체만 반환하고 마크다운 코드펜스를 사용하지 마라. '
            '스키마: {"title":"한국어 브리핑 제목","summary":"두 문장 이내 전체 요약",'
            '"items":[{"title":"제목","date":"YYYY-MM-DD","summary":"사실 요약 2~4문장",'
            '"implication":"실무 영향 및 검토 사항","url":"https://공식출처-원문"}]}.'
            '품질 우선, 상세 자료가 있으면 3~10건, 미확인 자료를 억지로 채우지 않는다. ''반드시 모든 문장을 정중한 보고서 문체(~습니다, ~합니다, ~입니다)로 종결하라. 해라체(~다, ~한다, ~했다, ~필요가 있다)로 끝내지 마라. ''summary와 implication에는 출처 도메인, URL, 마크다운 링크를 삽입하지 마라. 링크는 url 필드에만 넣어라.')


def request_ai(kind, start, end, extra_instructions=None):
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        raise RuntimeError('OPENAI_API_KEY secret is missing')
    data = {'model': os.getenv('OPENAI_MODEL', 'gpt-6.1-sol'),
            'tools': [{'type': 'web_search', **({'filters': {'allowed_domains': ['fss.or.kr']}} if kind == 'fss' else {})}],
            'include': ['web_search_call.action.sources'],
            'tool_choice': 'auto',
            'input': extra_instructions if extra_instructions is not None else instructions(kind, start, end),
            'max_output_tokens': 6500}
    req = urllib.request.Request(API, data=json.dumps(data, ensure_ascii=False).encode(),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        # Do not log Authorization header, key, or complete response body.
        raise RuntimeError(f'OpenAI API request failed (HTTP {e.code}); check model access, balance and permissions') from e
    if payload.get('status') not in ('completed', None):
        raise RuntimeError(f"API status: {payload.get('status')}; publishing stopped")
    pieces = [x.get('text','') for message in payload.get('output',[]) if message.get('type')=='message'
              for x in message.get('content',[]) if x.get('type') == 'output_text']
    text = '\n'.join(pieces).strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.I).strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError('AI response was not valid JSON; no files published') from e
    if not isinstance(result, dict) or not isinstance(result.get('items'), list):
        raise RuntimeError('AI response has unexpected structure; no files published')
    # AI-generated URLs alone are insufficient: compare with sources actually consulted by the web tool.
    cited = set()
    for entry in payload.get('output', []):
        if entry.get('type') == 'web_search_call':
            action = entry.get('action') or {}
            for source in action.get('sources') or []:
                if isinstance(source, dict) and source.get('url'):
                    cited.add(source['url'])
        if entry.get('type') == 'message':
            for content in entry.get('content') or []:
                for annotation in content.get('annotations') or []:
                    if annotation.get('type') == 'url_citation' and annotation.get('url'):
                        cited.add(annotation['url'])
    if not cited:
        raise RuntimeError('Web search returned no traceable cited source URLs; no publication')
    result['_cited_urls'] = cited
    return result



def request_ifrs_with_agency_fallback(start, end):
    """Search official channels in two rounds, then verify URLs in save().

    Primary round checks multiple agencies. If zero candidates, separate agency
    checks reduce empty results caused by one oversized/ambiguous search task.
    All rounds retain strict date/source validation; no invented articles.
    """
    print('IFRS_SEARCH: primary official-agency scan', flush=True)
    first = request_ai('ifrs', start, end)
    if first.get('items'):
        print(f'IFRS_SEARCH: primary returned {len(first["items"])} candidate(s)', flush=True)
        return first
    print('IFRS_SEARCH: primary returned 0; running 2 targeted official-agency rescans', flush=True)

    agency_queries = [
        ('KASB_FSC_FSS',
         f'한국시간 {start}~{end} 사이에 새로 발표된 공식 자료만 조사하라. '
         '우선 site:kasb.or.kr 에서 K-IFRS 제·개정, 공개초안, 기준서 해설과 공지 검색. '
         '별도로 site:fsc.go.kr 및 site:fss.or.kr 에서 내부회계관리제도, 회계감리, '
         '회계기준 감독, 감사 관련 당일 발표를 검색. 기관별로 여러 번 검색하라.'),
        ('IFRS_KICPA',
         f'한국시간 {start}~{end} 사이에 새로 발표된 공식 자료만 조사하라. '
         'site:ifrs.org 의 IASB/IFRS Foundation IFRS 회계기준 새 발표, '
         'site:kicpa.or.kr 의 공인회계사회 공식 내부회계/외감 자료를 각각 검색하라. '
         '국내 기업회계에 직접 영향 있는 새 발표만 포함한다.'),
    ]
    combined=[]
    urls=set(first.get('_cited_urls',set()))
    for label, focus in agency_queries:
        prompt=(focus + '\n' +
            '발표일은 반드시 공식 원문에서 직접 확인하고 검색 결과의 색인일과 혼동하지 마라. '
            '대상일이 아니거나 발표일이 불분명한 자료는 반드시 제외한다. '
            '다른 기관의 발표를 인용한 2차 기사와 일반 해설은 제외한다. '
            '자료가 확인되지 않을 경우 빈 items 배열을 반환하라. '
            '반드시 JSON 객체만 출력하라. 스키마: '
            '{"title":"K-IFRS·내부회계 일일 브리핑","summary":"전체 요약",'
            '"items":[{"title":"제목","date":"YYYY-MM-DD","summary":"확인된 사실 2~4문장",'
            '"implication":"실무 검토사항 2~3문장","url":"https://원문"}]}. '
            '한국어 문장은 모두 정중한 ~습니다/~합니다 문체로 작성하고 URL을 본문에 넣지 마라.')
        try:
            result=request_ai('ifrs', start, end, extra_instructions=prompt)
        except RuntimeError as exc:
            # Fail closed if the official-source scan cannot be completed.
            raise RuntimeError(f'IFRS_SEARCH {label} failed; publication aborted: {exc}') from exc
        print(f'IFRS_SEARCH: {label} returned {len(result["items"])} candidate(s)', flush=True)
        combined.extend(result.get('items', []))
        urls.update(result.get('_cited_urls',set()))
    uniq={}
    for item in combined:
        if isinstance(item,dict):
            key=(str(item.get('url','')),str(item.get('date','')))
            uniq.setdefault(key,item)
    print(f'IFRS_SEARCH: total {len(uniq)} unique candidate(s) after targeted rescans', flush=True)
    return {'title':'K-IFRS·내부회계 일일 브리핑',
            'summary':('공식기관별 검색에서 확인된 전일 발표 자료를 정리했습니다.' if uniq else
                       '공식기관별 재검색에서 검증 가능한 전일 신규 자료를 확인하지 못했습니다.'),
            'items':list(uniq.values()),'_cited_urls':urls}

def normalize_url(url):
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme.lower(), (parts.hostname or '').lower(), parts.path.rstrip('/'), parts.query)


def check_url(url, kind, cited):
    try:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or '').lower()
        if parts.scheme != 'https' or not host or parts.username or parts.password or parts.port not in (None, 443):
            return False
        if kind == 'fss' and host != 'fss.or.kr' and not host.endswith('.fss.or.kr'):
            return False
        # Model must point to a URL actually surfaced by the web-search result.
        if normalize_url(url) not in {normalize_url(u) for u in cited}:
            return False
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; BriefingVerifier/1.0)'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            final = urllib.parse.urlsplit(resp.geturl())
            final_host = (final.hostname or '').lower()
            if kind == 'fss' and final_host != 'fss.or.kr' and not final_host.endswith('.fss.or.kr'):
                return False
            mime = resp.headers.get('Content-Type','').lower()
            return 200 <= resp.status < 300 and ('text/html' in mime or 'application/pdf' in mime or 'application/octet-stream' in mime)
    except (ValueError, OSError, urllib.error.URLError):
        return False


def clean_items(items, start, end, kind, cited):
    usable, seen = [], set()
    for x in items:
        if not isinstance(x, dict):
            continue
        title, date, url = str(x.get('title','')).strip(), str(x.get('date','')).strip(), str(x.get('url','')).strip()
        if not (title and start <= date <= end and url.startswith('https://')):
            continue
        if any(ch in url for ch in '<>"\\') or ' ' in url:
            continue
        if url in seen or not check_url(url, kind, cited):
            continue
        seen.add(url)
        usable.append({k: str(x.get(k, '')).strip() for k in ('title','date','summary','implication','url')})
    return usable



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

def display_text(value):
    """Remove duplicated inline source links; the separate 원문 확인 button retains the URL."""
    value = str(value or '')
    value = re.sub(r'\s*\([^()\n]{1,100}\)\(https?://[^\s)]+\)', '', value)
    value = re.sub(r'\s*\(https?://[^\s)]+\)', '', value)
    value = re.sub(r'\s*https?://[^\s<>()]+', '', value)
    return formalize(re.sub(r'[ \t]{2,}', ' ', value).strip())


def page(kind, title, date, start, end, intro, items):
    rows = []
    for idx, x in enumerate(items, 1):
        rows.append(f'<article><div class="kicker">{idx:02} · {html.escape(x["date"])}</div>'
                    f'<h2>{html.escape(x["title"])}</h2>'
                    f'<p>{html.escape(display_text(x["summary"]))}</p>'
                    f'<div class="impact"><div class="impact-title">실무 영향 및 검토사항</div><p>{html.escape(display_text(x["implication"]))}</p></div>'
                    f'<a href="{html.escape(x["url"], quote=True)}" rel="noopener noreferrer" target="_blank">원문 확인 ↗</a></article>')
    body = '\n'.join(rows) if rows else '<article><h2>해당 기간에 확인된 신규 주요 자료가 없습니다.</h2><p>검색·수집 범위 내 결과이며 기관의 전체 발표 부재를 의미하지는 않습니다.</p></article>'
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>:root{{--green:#0099A8;--green-deep:#007985;--orange:#FDB914;--gray:#808080;--line:#DFE9EA}}body{{font-family:system-ui,'Noto Sans KR',sans-serif;background:#f6f9f9;color:#20363c;margin:0;line-height:1.75}}main{{max-width:850px;margin:auto;padding:34px 20px 70px}}a{{color:#007985}}header{{position:sticky;top:0;z-index:100;border-top:5px solid var(--green);border-bottom:1px solid var(--line);background:rgba(255,255,255,.97);backdrop-filter:blur(6px);padding:14px 20px;box-shadow:0 2px 12px rgba(16,45,50,.05)}}header>div{{max-width:850px;margin:auto;display:flex;align-items:center;min-height:34px}}.kicker{{color:var(--green);font-size:12px;font-weight:800;letter-spacing:1px}}h1{{font-size:clamp(26px,4vw,38px);line-height:1.28}}article{{background:#fff;border:1px solid #e1ecee;border-radius:14px;padding:25px;margin:16px 0}}article h2{{font-size:20px;margin:8px 0}}.impact{{background:#F2F4F5;border:1px solid #E4E8E9;border-radius:10px;padding:16px 18px;margin:18px 0}}.impact-title{{font-size:13px;font-weight:800;color:#007985;margin-bottom:7px}}.impact p{{margin:0}}.meta{{color:var(--gray)}}.notice{{border-left:4px solid var(--orange);background:white;padding:12px 18px;color:#566}}.back{{display:inline-flex;align-items:center;gap:6px;text-decoration:none;font-weight:800;color:#0b636c}}.back:hover{{text-decoration:underline}}@media(max-width:600px){{header{{padding:12px 16px}}main{{padding:24px 16px 56px}}article{{padding:18px}}}}</style></head><body>
<header><div><a class="back" href="../index.html">← 회계 및 내부회계 AI브리핑</a></div></header><main><div class="kicker">{html.escape(CATEGORIES[kind])}</div>
<h1>{html.escape(title)}</h1><p class="meta">대상기간 {start} ~ {end} · 작성일 {date}</p><p>{html.escape(display_text(intro))}</p><div class="notice">AI가 공개자료를 바탕으로 작성한 요약입니다. 중요한 회계·법률 판단은 원문 및 전문가 검토가 필요합니다.</div>{body}</main></body></html>'''

def save(kind, now, start, end, result):
    items = clean_items(result['items'], start, end, kind, result.get('_cited_urls', set()))
    print(f'Validation: AI returned {len(result["items"])} candidate(s), {len(result.get("_cited_urls", []))} cited URL(s), {len(items)} passed.', flush=True)
    # Never publish a 'no news' article when we cannot prove coverage was complete.
    if not items:
        if kind == 'ifrs':
            print('NO_VERIFIED_ITEMS: no verified prior-day announcements; leaving website unchanged. This does not prove no announcements existed.', flush=True)
            return
        raise RuntimeError('SOURCE_UNAVAILABLE: no verified sources in the search window. This is not proof that no press releases were published; no publication.')
    # Do not re-publish previously published original source URLs.
    prior_urls = set()
    for old_page in (ROOT / 'briefings').glob(f'{kind}-*.html'):
        try:
            old_html = old_page.read_text(encoding='utf-8')
            prior_urls.update(html.unescape(u) for u in re.findall(r'href="(https://[^"]+)"', old_html))
        except OSError:
            pass
    before = len(items)
    items = [item for item in items if item['url'] not in prior_urls]
    print(f'Deduplication: removed {before-len(items)} already-published source(s).', flush=True)
    if not items:
        print('NO_NEW_ITEMS: verified sources were already published; leaving website unchanged.', flush=True)
        return
    title = f'{CATEGORIES[kind]} | {now.date().isoformat()}' if kind in ('fss','ifrs') else f'{CATEGORIES[kind]} | {start}~{end}'
    summary = formalize(str(result.get('summary', '')).strip())[:280]
    slug = f'{kind}-{now.date().isoformat()}'
    url = f'briefings/{slug}.html'
    posts_file = ROOT/'posts.json'
    try:
        posts = json.loads(posts_file.read_text(encoding='utf-8'))
        if not isinstance(posts, list):
            raise ValueError('not array')
    except FileNotFoundError:
        posts = []
    except (json.JSONDecodeError, ValueError) as e:
        raise RuntimeError('posts.json invalid; refusing overwrite') from e
    dst = ROOT / url
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(page(kind, title, now.date().isoformat(), start, end, summary, items), encoding='utf-8')
    posts = [p for p in posts if p.get('url') != url]
    posts.append({'category':kind,'date':now.date().isoformat(),'title':title,'summary':summary,'url':url})
    posts.sort(key=lambda p:p.get('date',''),reverse=True)
    posts_file.write_text(json.dumps(posts, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f'Created {url}: {len(items)} cited, HTTPS-reachable items. Publication dates/content still require editorial checking.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=[*CATEGORIES, 'weekly'], required=True)
    args = parser.parse_args()
    now = dt.datetime.now(TZ)
    kinds = ['ifrs','security'] if args.kind == 'weekly' else [args.kind]
    for kind in kinds:
        start,end = time_range(kind,now)
        print(f'Generating {kind}: {start} to {end}')
        result = request_ifrs_with_agency_fallback(start,end) if kind == 'ifrs' else request_ai(kind,start,end)
        save(kind,now,start,end,result)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
