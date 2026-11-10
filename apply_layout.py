from pathlib import Path

p=Path('index.html')
s=p.read_text(encoding='utf-8')
if '/* BRIEFING_THREE_COLUMN_V1 */' in s:
    print('Layout already applied'); raise SystemExit(0)
start=s.find('      <section class="quicklinks" aria-label="업무 사이트 바로가기">')
end=s.find('    </aside>',start)
if start<0 or end<0: raise SystemExit('Expected quicklinks/sidebar structure not found. No changes made.')
quick=s[start:end]
s=s[:start]+s[end:]
anchor='  </main>'
if s.count(anchor)!=1: raise SystemExit('Unexpected main structure')
s=s.replace(anchor,'    '+quick.lstrip() + anchor,1)
css='''
    /* BRIEFING_THREE_COLUMN_V1: PC - services | library | quick links */
    @media (min-width:851px) {
      .container {width:min(1600px,calc(100% - 36px))}
      .dashboard {grid-template-columns:minmax(225px,280px) minmax(0,1fr) minmax(185px,225px);gap:18px;align-items:start}
      .sidebar {grid-column:1;grid-row:1}
      .archive {grid-column:2;grid-row:1}
      .quicklinks {grid-column:3;grid-row:1;margin-top:0;align-self:start;padding:16px 14px}
      .quicklinks-list {grid-template-columns:1fr}
      .hero h1 {font-size:clamp(21px,2vw,26px);white-space:normal;overflow-wrap:keep-all}
      .service {padding:13px 13px}
      .service h2 {font-size:15px}
      .quicklink {padding:9px;min-height:52px}
      .quicklink b {font-size:11px}
      .quicklink small {font-size:9px}
    }
    @media (min-width:851px) and (max-width:1150px) {
      .dashboard {grid-template-columns:minmax(210px,240px) minmax(0,1fr) minmax(170px,195px);gap:12px}
      .archive {padding:19px 16px}
      .quicklinks {padding:12px 10px}
    }
'''
s=s.replace('  </style>',css+'  </style>',1)
p.write_text(s,encoding='utf-8')
print('Updated index.html, quicklinks moved into third column; mobile retains bottom placement.')
