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
CATEGORIES = {'fss': '금융감독원 일일 브리핑', 'ifrs': 'K-IFRS·내부회계 주간 브리핑', 'security': '보안시장 주간 동향'}


def time_range(kind, now):
    if kind == 'fss':
        day = now.date()
        return day.isoformat(), day.isoformat()
    monday = now.date() - dt.timedelta(days=now.weekday() + 7)
    return monday.isoformat(), (monday + dt.timedelta(days=6)).isoformat()


def instructions(kind, start, end):
    prefix = f'한국시간 기준 대상기간: {start} ~ {end}. 이 기간 중 실제 발표일을 확인한 신규 공개 자료만 포함하라. 오늘 발표 내용은 아직 없을 수도 있다.\n'
    if kind == 'fss':
        domain = '금융감독원 공식 보도자료를 우선 확인. 모든 보도자료 중 회계·감사·공시·감리·내부회계 관련 자료를 우선 배치하고, 그 외 중요한 금융감독원 발표도 누락하지 않도록 한다.'
    elif kind == 'ifrs':
        domain = 'K-IFRS/IFRS와 내부회계관리제도 관련 공식 개정, 금융감독원·한국회계기준원·IFRS Foundation 발표, 감사·감리 동향을 확인한다. 특히 K-IFRS 1115,1002,1116,1019, IFRS 18, 연결내부회계, ITGC 실무 영향을 분석한다.'
    else:
        domain = '국내외 CCTV/IP카메라, VMS/NVR, AI 영상분석, VSaaS, 출입통제, 이미지센서, 입찰/RFP, 규제 및 경쟁사(Axis, Hanwha Vision, Hikvision, Dahua, i-PRO, Genetec, Milestone) 동향을 확인하고 제조사 관점의 시사점을 분석한다.'
    return (prefix + domain + '\n웹 검색을 사용해 실제 원문 링크를 찾아라. 출처가 없는 문서는 절대 생성하지 마라. 불확실한 내용은 제외하라. '
            '동일 사건 중복 제거. 대상기간에 근거 있는 자료가 없다면 items를 빈 배열로 반환하라. '
            '출처 웹 주소는 실제 원문 URL을 그대로 사용하라. 현재 운영 목적은 시험용 자동 브리핑이다. '
            '반드시 JSON 객체만 반환하고 마크다운 코드펜스를 사용하지 마라. '
            '스키마: {"title":"한국어 브리핑 제목","summary":"두 문장 이내 전체 요약",'
            '"items":[{"title":"제목","date":"YYYY-MM-DD","summary":"사실 요약 2~4문장",'
            '"implication":"실무 영향 및 검토 사항","url":"https://공식출처-원문"}]}.'
            '품질 우선, 상세 자료가 있으면 3~10건, 미확인 자료를 억지로 채우지 않는다.')


def request_ai(kind, start, end):
    key = os.getenv('OPENAI_API_KEY')
    if not key:
        raise RuntimeError('OPENAI_API_KEY secret is missing')
    data = {'model': os.getenv('OPENAI_MODEL', 'gpt-6.1-sol'),
            'tools': [{'type': 'web_search', **({'filters': {'allowed_domains': ['fss.or.kr']}} if kind == 'fss' else {})}],
            'include': ['web_search_call.action.sources'],
            'tool_choice': 'auto',
            'input': instructions(kind, start, end),
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


def page(kind, title, date, start, end, intro, items):
    rows = []
    for idx, x in enumerate(items, 1):
        rows.append(f'<article><div class="kicker">{idx:02} · {html.escape(x["date"])}</div>'
                    f'<h2>{html.escape(x["title"])}</h2>'
                    f'<p>{html.escape(x["summary"])}</p>'
                    f'<p><strong>실무 영향</strong> {html.escape(x["implication"])}</p>'
                    f'<a href="{html.escape(x["url"], quote=True)}" rel="noopener noreferrer" target="_blank">원문 확인 ↗</a></article>')
    body = '\n'.join(rows) if rows else '<article><h2>해당 기간에 확인된 신규 주요 자료가 없습니다.</h2><p>검색·수집 범위 내 결과이며 기관의 전체 발표 부재를 의미하지는 않습니다.</p></article>'
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>:root{{--green:#0099A8;--orange:#FDB914;--gray:#808080}}body{{font-family:system-ui,'Noto Sans KR',sans-serif;background:#f6f9f9;color:#20363c;margin:0;line-height:1.75}}main{{max-width:850px;margin:auto;padding:30px 20px 70px}}a{{color:#007985}}header{{border-top:5px solid var(--green);background:white;padding:20px}}header>div{{max-width:850px;margin:auto}}.kicker{{color:var(--green);font-size:12px;font-weight:800;letter-spacing:1px}}h1{{font-size:clamp(26px,4vw,38px);line-height:1.28}}article{{background:#fff;border:1px solid #e1ecee;border-radius:14px;padding:25px;margin:16px 0}}article h2{{font-size:20px;margin:8px 0}}.meta{{color:var(--gray)}}.notice{{border-left:4px solid var(--orange);background:white;padding:12px 18px;color:#566}}.back{{text-decoration:none;font-weight:700}}@media(max-width:600px){{article{{padding:18px}}}}</style></head><body>
<header><div><a class="back" href="../index.html">← 회계 및 내부회계 AI브리핑</a></div></header><main><div class="kicker">{html.escape(CATEGORIES[kind])}</div>
<h1>{html.escape(title)}</h1><p class="meta">대상기간 {start} ~ {end} · 작성일 {date}</p><p>{html.escape(intro)}</p><div class="notice">AI가 공개자료를 바탕으로 작성한 요약입니다. 중요한 회계·법률 판단은 원문 및 전문가 검토가 필요합니다.</div>{body}</main></body></html>'''


def save(kind, now, start, end, result):
    items = clean_items(result['items'], start, end, kind, result.get('_cited_urls', set()))
    # Never publish a 'no news' article when we cannot prove coverage was complete.
    if not items:
        raise RuntimeError('No reachable, cited source from the required date range; no publication. Check source availability and dates.')
    title = f'{CATEGORIES[kind]} | {start}' if kind == 'fss' else f'{CATEGORIES[kind]} | {start}~{end}'
    summary = str(result.get('summary', '')).strip()[:280]
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
        result = request_ai(kind,start,end)
        save(kind,now,start,end,result)

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f'ERROR: {e}', file=sys.stderr)
        sys.exit(1)
