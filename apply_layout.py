from pathlib import Path
import re

path = Path(__file__).resolve().parent / 'index.html'
if not path.exists():
    raise SystemExit('ERROR: index.html not found alongside apply_layout.py')
s = path.read_text(encoding='utf-8')
original = s

# Locate the link section without depending on the exact whitespace/attribute order.
match = re.search(r'(?P<indent>^[ \t]*)<section\b(?=[^>]*\bclass\s*=\s*["\'][^"\']*\bquicklinks\b)[^>]*>.*?</section\s*>', s, re.M | re.S | re.I)
if not match:
    raise SystemExit('ERROR: No .quicklinks section found. Original index.html was not changed.')
quicklinks = match.group(0).lstrip()
s_without = s[:match.start()] + s[match.end():]

# The archive is a top-level child of <main>, and </main> follows it.
# Put the extracted quicklinks after the archive but still inside the dashboard.
main_close = re.search(r'^[ \t]*</main\s*>', s_without, re.M | re.I)
if not main_close:
    raise SystemExit('ERROR: </main> missing. Original index.html was not changed.')
# Keep mobile layout functional: sidebar display:contents; archive order:3; quicklinks order:4.
s = s_without[:main_close.start()] + '    ' + quicklinks + '\n' + s_without[main_close.start():]

css = '''
    /* BRIEFING_THREE_COLUMN_V2 - desktop: categories | archive | reference links */
    @media (min-width:1151px) {
      .container {width:min(1680px,calc(100% - 40px))}
      .dashboard {grid-template-columns:minmax(245px,280px) minmax(0,1fr) minmax(210px,250px);gap:18px;align-items:start}
      .sidebar {grid-column:1;grid-row:1}
      .archive {grid-column:2;grid-row:1}
      .quicklinks {grid-column:3;grid-row:1;margin-top:0;align-self:start;padding:17px 14px}
      .quicklinks-list {grid-template-columns:1fr}
      .hero h1 {white-space:normal;font-size:clamp(22px,2vw,27px)}
      .service {padding:13px}
      .service h2 {font-size:15px}
      .quicklink {padding:10px;min-height:52px}
    }
    @media (min-width:851px) and (max-width:1150px) {
      .dashboard {grid-template-columns:minmax(230px,260px) minmax(0,1fr)}
      .quicklinks {grid-column:1/-1;grid-row:2;margin-top:0}
      .quicklinks-list {grid-template-columns:repeat(4,minmax(0,1fr))}
    }
    @media (max-width:850px) {
      .quicklinks {order:4;margin:13px 0 4px}
    }
'''
s = re.sub(r'\s*/\* BRIEFING_THREE_COLUMN_V\d+.*?\*/\s*(?=@media)', '\n', s, flags=re.S) if False else s
# Avoid accumulating duplicate overrides if a previous successful version was applied.
if '/* BRIEFING_THREE_COLUMN_V2' not in s:
    s = s.replace('  </style>', css + '  </style>', 1)

# Validate structure before saving.
assert s.count('class="quicklinks"') == 1, 'Quicklinks unexpectedly duplicated'
assert s.count('class="archive"') == 1, 'Archive unexpectedly duplicated'
assert s.index('class="archive"') < s.index('class="quicklinks"') < s.index('</main>'), 'Wrong ordering'
assert 'BRIEFING_THREE_COLUMN_V2' in s
path.write_text(s, encoding='utf-8')
print('SUCCESS: index.html updated; desktop uses three columns, mobile quicklinks at bottom.')
print(f'Bytes before/after: {len(original.encode())} / {len(s.encode())}')
