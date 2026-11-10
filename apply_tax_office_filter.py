#!/usr/bin/env python3
"""One-time patch for taemin2022/accounting-briefing.
Run from repository root: python apply_tax_office_filter.py
No API keys or external dependencies required. Idempotent.
"""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / 'tax_daily.py'
MARKER = '# LOCAL_TAX_OFFICE_EXCLUSION_V1'

HELPER = '''# LOCAL_TAX_OFFICE_EXCLUSION_V1
# Exclude district tax-office announcements before AI review and publication.
# The National Tax Service headquarters and taxlaw information remain eligible.
def excluded_local_tax_office(record):
    from urllib.parse import urlsplit
    url = str(record.get('url', '')).strip()
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or '').lower()
    except ValueError:
        return True
    source = clean(record.get('source', '')).replace(' ', '')
    title = clean(record.get('title', '')).replace(' ', '')
    excerpt = clean(record.get('excerpt', ''))[:200].replace(' ', '')
    # Local NTS office pages are served under t.nts.go.kr/<office>/...
    if host == 't.nts.go.kr':
        return True
    # Other local offices sometimes have office-specific subdomains.
    if host.endswith('.nts.go.kr') and host not in ('www.nts.go.kr', 'taxlaw.nts.go.kr', 'nts.go.kr'):
        return True
    # Explicit source provenance (but don't reject a national law article
    # merely because its content mentions a local office).
    if source.endswith('세무서') or source.endswith('지방국세청'):
        return True
    if re.search(r'(구미|상주|강남|서초|중부|종로|용산|영등포|동대문|동작|성동|송파|마포|강서|양천|금천|관악|잠실|노원|도봉|강북|성북|은평|서대문|남대문|반포|역삼|삼성|수원|용인|분당|성남|기흥|동수원|안양|안산|부천|인천|대구|동대구|서대구|북대구|남대구|부산|광주|대전|울산|창원|전주|청주|천안|제주)세무서', title + excerpt):
        # Avoid excluding a headquarters-issued legal update that simply
        # quotes a tax office; only classify generic administrative notices.
        administrative = ('공지', '안내', '행사', '환급금', '상담', '교육', '민원', '운영', '신청')
        substantive = ('개정', '시행령', '예규', '질의회신', '판결', '심판례', '유권해석')
        if any(x in title for x in administrative) and not any(x in title for x in substantive):
            return True
    return False

'''

def replace_once(text, old, new, label):
    if old not in text:
        if new in text:
            return text
        raise RuntimeError(f'Expected anchor not found: {label}; refusing unsafe edit')
    return text.replace(old, new, 1)

def patch_tax_daily():
    if not TARGET.exists():
        raise RuntimeError('Put this file in the same folder as tax_daily.py')
    code = TARGET.read_text(encoding='utf-8')
    if MARKER in code:
        print('ALREADY_PATCHED tax_daily.py')
        return
    code = replace_once(code, 'def preliminary_candidate(record):', HELPER + 'def preliminary_candidate(record):', 'insert office filter')
    code = replace_once(code, "if not title or excluded_yearend_simplification(record):", "if not title or excluded_yearend_simplification(record) or excluded_local_tax_office(record):", 'preliminary filter')
    code = replace_once(code, "if not url or not official_host(url,domains):", "if not url or not official_host(url,domains) or excluded_local_tax_office(x):", 'post-search source filter')
    code = replace_once(code, "연말정산 간소화자료, 간소화자료 일괄제공 서비스 및 세무서별 신청·운영 안내는 수집 대상에서 제외하십시오.",
        "연말정산 간소화자료와 개별 세무서(구미·상주세무서 등 전국 세무서) 공지사항 전체는 제외하십시오. "
        "특히 t.nts.go.kr 하위의 세무서별 게시물은 결과에 절대 포함하지 마십시오. "
        "국세청 본청(www.nts.go.kr) 발표와 국세법령정보시스템(taxlaw.nts.go.kr)의 법령·예규·해석례는 유지하십시오.", 'search prompt')
    code = replace_once(code, "단, 할인행사·축제·쿠폰·소비촉진·기관 홍보·단순 방문·해외직구 민원은 제외하십시오.",
        "전국 개별 세무서 및 지방국세청의 일반 공지, 환급금 확인·수령 안내는 반드시 제외하십시오. "
        "단, 국세청 본청이 직접 게시한 세법 개정·공식 예규·해석례는 포함 대상입니다. "
        "단, 할인행사·축제·쿠폰·소비촉진·기관 홍보·단순 방문·해외직구 민원은 제외하십시오.", 'review prompt')
    # Defense in depth: no local-office records should enter the AI review.
    code = replace_once(code, "def analyze(records):\n    '''Review all candidates, validating a verdict for each supplied ID.'''",
        "def analyze(records):\n    '''Review all candidates, validating a verdict for each supplied ID.'''\n    records = [r for r in records if not excluded_local_tax_office(r)]", 'review gate')
    # Defense in depth: final publication gate.
    code = replace_once(code, "def publish(today,start,end,summary,items,ids):\n    # Existing site-compatible report renderer from briefing.py.",
        "def publish(today,start,end,summary,items,ids):\n    # Existing site-compatible report renderer from briefing.py.\n    items = [r for r in items if not excluded_local_tax_office(r)]\n    if not items:\n        print('LOCAL_OFFICE_ONLY: no eligible report to publish', flush=True)\n        return", 'publication gate')
    compile(code, str(TARGET), 'exec')
    TARGET.write_text(code, encoding='utf-8')
    print('PATCHED tax_daily.py: search, early filter, review, final publication')


def remove_unwanted_nov10():
    """Remove ONLY the confirmed Nov 10 duplicate local-office report.
    Keep every other day's report and all seen URLs intact.
    """
    relative = 'briefings/tax-2026-11-10.html'
    report = ROOT / relative
    if not report.exists():
        print('NOV10 report absent; nothing to remove')
        return
    body = report.read_text(encoding='utf-8')
    articles = re.findall(r'<article(?:\s[^>]*)?>.*?</article>', body, flags=re.S|re.I)
    local_only = (len(articles) == 2 and all(
        '미수령 환급금 확인 방법 안내' in article and
        ('t.nts.go.kr/gumi/' in article or 't.nts.go.kr/sangju/' in article)
        for article in articles))
    if not local_only:
        print('NOV10 report has different/additional contents; preserved for manual review')
        return
    posts_path = ROOT / 'posts.json'
    if not posts_path.exists():
        raise RuntimeError('posts.json missing; nothing removed')
    posts = json.loads(posts_path.read_text(encoding='utf-8'))
    if not isinstance(posts, list):
        raise RuntimeError('posts.json is not a list; nothing removed')
    keep = [p for p in posts if not (isinstance(p, dict) and p.get('category') == 'tax' and p.get('url') == relative)]
    excluded_path = ROOT / 'tax_excluded_reports.json'
    excluded = json.loads(excluded_path.read_text(encoding='utf-8')) if excluded_path.exists() else []
    if not isinstance(excluded, list):
        raise RuntimeError('tax_excluded_reports.json invalid; nothing removed')
    excluded = sorted(set(excluded) | {'2026-11-10'})
    posts_path.write_text(json.dumps(keep, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    excluded_path.write_text(json.dumps(excluded, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report.unlink()
    print('REMOVED 2026-11-10 local-only report, index entry, and prevented republishing')


def main():
    patch_tax_daily()
    remove_unwanted_nov10()

if __name__ == '__main__':
    main()
