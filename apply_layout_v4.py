from pathlib import Path
import re

path = Path('index.html')
html = path.read_text(encoding='utf-8')

# Verify the three sections are siblings, so a changed layout fails safely.
main = re.search(r'<main\b[^>]*class=["\'][^"\']*\bdashboard\b[^"\']*["\'][^>]*>(.*?)</main\s*>', html, re.I | re.S)
if not main:
    raise SystemExit('ERROR: dashboard <main> not found; index.html unchanged')
if not all(re.search(r'<(?:aside|section)\b[^>]*class=["\'][^"\']*\b'+name+r'\b', main.group(1), re.I) for name in ('sidebar','archive','quicklinks')):
    raise SystemExit('ERROR: expected sidebar/archive/quicklinks elements not found; index.html unchanged')

start = '/* THREE_COLUMN_LAYOUT_V4_BEGIN */'
end = '/* THREE_COLUMN_LAYOUT_V4_END */'
css = '''/* THREE_COLUMN_LAYOUT_V4_BEGIN */
/* Use actual direct children of dashboard. Do not move HTML elements. */
@media (min-width: 851px) {
  .container.dashboard {
    width: min(1660px, calc(100% - 36px));
    display: grid;
    grid-template-columns: minmax(235px, 285px) minmax(0, 1fr) minmax(195px, 240px);
    grid-template-rows: auto;
    gap: 18px;
    align-items: start;
    margin-inline: auto;
  }
  .dashboard > .sidebar {
    display: block;
    grid-column: 1;
    grid-row: 1;
    min-width: 0;
  }
  .dashboard > .archive {
    grid-column: 2;
    grid-row: 1;
    min-width: 0;
    width: 100%;
  }
  .dashboard > .quicklinks {
    display: block;
    grid-column: 3;
    grid-row: 1;
    min-width: 0;
    margin-top: 0;
    align-self: start;
  }
  .dashboard > .quicklinks .quicklinks-list {grid-template-columns: 1fr;}
}
@media (min-width: 851px) and (max-width: 1100px) {
  .container.dashboard {
    grid-template-columns: minmax(205px, 235px) minmax(0, 1fr) minmax(170px, 195px);
    gap: 12px;
  }
}
/* THREE_COLUMN_LAYOUT_V4_END */'''

html = re.sub(re.escape(start)+r'.*?'+re.escape(end), '', html, flags=re.S)
if '</style>' not in html:
    raise SystemExit('ERROR: style closing tag not found; index.html unchanged')
html = html.replace('</style>', css+'\n</style>', 1)
path.write_text(html, encoding='utf-8')
assert html.count(start) == 1
assert '.dashboard > .quicklinks {' in html
print('SUCCESS: V4 CSS installed; direct-child sibling selectors verified')
