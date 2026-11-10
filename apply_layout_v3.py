from pathlib import Path
import re

path = Path('index.html')
if not path.is_file():
    raise SystemExit('ERROR: index.html not found at repository root')
html = path.read_text(encoding='utf-8')
required = ['class="container dashboard"', 'class="sidebar"', 'class="quicklinks"', 'class="archive"', '</style>']
missing = [x for x in required if x not in html]
if missing:
    raise SystemExit('ERROR: expected layout elements missing: ' + ', '.join(missing))

css = '''/* THREE_COLUMN_LAYOUT_V3_BEGIN: stable desktop-only CSS grid placement */
@media (min-width: 1101px) {
  .container.dashboard {
    width: min(1660px, calc(100% - 44px));
    margin-inline: auto;
    display: grid;
    grid-template-columns: minmax(260px, 305px) minmax(0, 1fr) minmax(225px, 265px);
    grid-template-rows: auto auto minmax(0, 1fr);
    column-gap: 18px;
    row-gap: 0;
    align-items: start;
  }
  .dashboard > .sidebar { display: contents; }
  .dashboard > .sidebar > .nav { grid-column: 1; grid-row: 1; }
  .dashboard > .sidebar > .hero { grid-column: 1; grid-row: 2; }
  .dashboard > .sidebar > .services { grid-column: 1; grid-row: 3; }
  .dashboard > .archive { grid-column: 2; grid-row: 1 / 4; width: 100%; }
  .dashboard > .sidebar > .quicklinks {
    grid-column: 3;
    grid-row: 1 / 4;
    margin-top: 0;
    align-self: start;
    min-width: 0;
  }
}
/* THREE_COLUMN_LAYOUT_V3_END */'''
pattern = r'/\* THREE_COLUMN_LAYOUT_V3_BEGIN.*?THREE_COLUMN_LAYOUT_V3_END \*/'
if re.search(pattern, html, flags=re.S):
    html = re.sub(pattern, css, html, flags=re.S)
else:
    html = html.replace('</style>', css + '\n  </style>', 1)
path.write_text(html, encoding='utf-8')
assert css in path.read_text(encoding='utf-8')
print('SUCCESS: three-column desktop CSS inserted and verified in index.html')
