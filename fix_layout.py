from pathlib import Path
from html.parser import HTMLParser
import re

path=Path('index.html')
html=path.read_text(encoding='utf-8')
class LayoutCheck(HTMLParser):
    def __init__(self):
        super().__init__(); self.stack=[]; self.parents={}
    def handle_starttag(self, tag, attrs):
        a=dict(attrs); classes=set(a.get('class','').split())
        for cls in ('dashboard','sidebar','archive','quicklinks'):
            if cls in classes: self.parents[cls]=[x[1] for x in self.stack if x[1]]
        if tag not in ('meta','link','input','img','br','hr','source','area','embed','wbr'):
            self.stack.append((tag,next((c for c in ('dashboard','sidebar','archive','quicklinks') if c in classes),None)))
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                del self.stack[i:];return
checker=LayoutCheck();checker.feed(html)
for item in ('dashboard','sidebar','archive','quicklinks'):
    if item not in checker.parents: raise SystemExit(f'Missing element: {item}; no changes made')
for item in ('sidebar','archive','quicklinks'):
    if 'dashboard' not in checker.parents[item]:raise SystemExit(f'{item} is not under dashboard; no changes made')
if 'sidebar' in checker.parents['quicklinks']:
    raise SystemExit('quicklinks nested in sidebar; expected sibling. No changes made')
# The previous patches were appended at the bottom of the style sheet.
# Remove the whole appended patch region rather than stacking a fifth override.
start=html.find('/* BRIEFING_THREE_COLUMN_V1:')
if start!=-1:
    end=html.find('</style>',start)
    if end==-1:raise SystemExit('Missing closing style tag')
    html=html[:start]+html[end:]
# Clean up previous V4 and/or this patch on reruns if they appear independently.
html=re.sub(r'/\* FINAL_LAYOUT_CLEAN_BEGIN \*/.*?/\* FINAL_LAYOUT_CLEAN_END \*/\s*','',html,flags=re.S)
css='''
    /* FINAL_LAYOUT_CLEAN_BEGIN */
    /* Keep actual DOM siblings: sidebar | archive | quicklinks */
    @media (min-width:851px) {
      main.container.dashboard {
        width:min(1680px,calc(100% - 40px));
        display:grid;
        grid-template-columns:minmax(220px,280px) minmax(0,1fr) minmax(190px,245px);
        gap:18px;
        align-items:start;
      }
      main.dashboard > .sidebar {display:block;grid-column:1;grid-row:1;min-width:0}
      main.dashboard > .archive {grid-column:2;grid-row:1;min-width:0;width:100%}
      main.dashboard > .quicklinks {grid-column:3;grid-row:1;margin-top:0;align-self:start;min-width:0}
      main.dashboard > .quicklinks .quicklinks-list {grid-template-columns:1fr}
      main.dashboard .hero h1 {white-space:normal;overflow-wrap:keep-all;font-size:clamp(20px,2vw,27px)}
      main.dashboard .service {padding:13px}
      main.dashboard .service h2 {font-size:15px}
      main.dashboard .quicklink {padding:9px;min-height:52px}
      main.dashboard .quicklink b {font-size:11px}
      main.dashboard .quicklink small {font-size:9px}
    }
    @media (min-width:851px) and (max-width:1150px) {
      main.container.dashboard {grid-template-columns:minmax(185px,230px) minmax(0,1fr) minmax(160px,190px);gap:12px}
      main.dashboard > .archive {padding:19px 16px}
      main.dashboard > .quicklinks {padding:12px 10px}
    }
    /* FINAL_LAYOUT_CLEAN_END */
'''
if html.count('</style>')!=1:raise SystemExit('Unexpected style tag count')
html=html.replace('</style>',css+'  </style>',1)
assert 'FINAL_LAYOUT_CLEAN_BEGIN' in html
assert 'BRIEFING_THREE_COLUMN_V1' not in html
path.write_text(html,encoding='utf-8')
print('SUCCESS: cleaned old CSS patches, applied verified direct-sibling 3-column layout')
print('Structure verified: dashboard > sidebar + archive + quicklinks; mobile rules retained')
