# -*- coding: utf-8 -*-
"""docs/REQUIREMENTS_AND_FUNCTIONAL_SPEC.md -> docs/SPEC.html

사양서 markdown을 사내 SRS 스타일의 정적 HTML로 변환한다 (사이드바 목차, 사양번호
뱃지, 상태 기호 뱃지, 스크린샷 프레임). 외부 CDN 의존 없이 로컬에서 파일로 열어
본다. 사양서(.md)를 고친 뒤 다시 실행하면 SPEC.html이 갱신된다.

사용법:
  .venv/Scripts/python.exe -m pip install markdown   # 최초 1회
  .venv/Scripts/python.exe scripts/build_spec_html.py

`markdown` 패키지는 이 문서 빌드 스크립트 전용이라 requirements.txt(앱 런타임 의존성)에는
넣지 않았다.
"""
import re
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "REQUIREMENTS_AND_FUNCTIONAL_SPEC.md"
OUT = ROOT / "docs" / "SPEC.html"

text = SRC.read_text(encoding="utf-8")


def h2_repl(m):
    return f'## <span class="secno">{m.group(1)}</span> {m.group(2)}'


def h3_repl(m):
    return f'### <span class="secno">{m.group(1)}</span> {m.group(2)}'


text = re.sub(r"^## (\d{2})\.\s+(.+)$", h2_repl, text, flags=re.MULTILINE)
text = re.sub(r"^### (\d{2}-\d{2})\s+(.+)$", h3_repl, text, flags=re.MULTILINE)

md = markdown.Markdown(
    extensions=["tables", "fenced_code", "sane_lists", "toc", "attr_list"],
    extension_configs={"toc": {"permalink": False, "anchorlink": False}},
)
body_html = md.convert(text)

BADGE_MAP = {
    "✅": "st-done",
    "◐": "st-partial",
    "○": "st-todo",
    "✋": "st-wontdo",
}
for sym, cls in BADGE_MAP.items():
    body_html = body_html.replace(sym, f'<span class="stbadge {cls}">{sym}</span>')


def img_repl(m):
    alt, src = m.group("alt"), m.group("src")
    cap = f"<figcaption>{alt}</figcaption>" if alt else ""
    return f'<figure class="shot"><img src="{src}" alt="{alt}" loading="lazy">{cap}</figure>'


body_html = re.sub(
    r'<img alt="(?P<alt>[^"]*)" src="(?P<src>[^"]+)"[^>]*/?>',
    img_repl,
    body_html,
)


def top_level_sections(tokens):
    # toc_tokens는 트리 구조다: 최상위에 문서 제목(h1) 토큰 하나가 있고, 실제 h2
    # 목록은 그 토큰의 children 안에 들어있다 (h3는 각 h2의 children 안에 중첩).
    if len(tokens) == 1 and tokens[0]["level"] == 1:
        return tokens[0]["children"]
    return tokens


def render_toc(tokens):
    tokens = top_level_sections(tokens)
    out = ['<ul class="toc-root">']
    for t in tokens:
        out.append(f'<li class="toc-l{t["level"]}"><a href="#{t["id"]}">{t["name"]}</a>')
        children = [c for c in t.get("children", []) if c["level"] <= 3]
        if children:
            out.append("<ul>")
            for c in children:
                out.append(
                    f'<li class="toc-l{c["level"]}"><a href="#{c["id"]}">{c["name"]}</a></li>'
                )
            out.append("</ul>")
        out.append("</li>")
    out.append("</ul>")
    return "\n".join(out)


sidebar_html = render_toc(md.toc_tokens)

CSS = """
:root{
  --bg:#f4f6f8; --panel:#ffffff; --ink:#1b2430; --muted:#5b6b7a;
  --line:#e1e6ea; --accent:#0b5cab; --accent-soft:#e8f1fb;
  --done:#1a7f4b; --done-bg:#e6f6ec;
  --partial:#a15c00; --partial-bg:#fdf0dc;
  --todo:#5b6b7a; --todo-bg:#eef1f4;
  --wontdo:#b0231e; --wontdo-bg:#fbe9e8;
  --code-bg:#f0f2f4;
  --sidebar-w:320px;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);
  font-family:"Segoe UI","Noto Sans KR","Malgun Gothic",sans-serif;
  font-size:15px;line-height:1.7;}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}

.layout{display:flex;align-items:flex-start;min-height:100vh}

.sidebar{position:sticky;top:0;height:100vh;width:var(--sidebar-w);flex:0 0 var(--sidebar-w);
  background:#12202e;color:#cfe0ee;overflow-y:auto;padding:20px 0 60px;}
.sidebar .brand{padding:0 20px 16px;border-bottom:1px solid #263b4d;margin-bottom:12px}
.sidebar .brand .t1{font-size:13px;color:#7fa8cc;letter-spacing:.04em;text-transform:uppercase}
.sidebar .brand .t2{font-size:17px;font-weight:700;color:#fff;margin-top:4px}
.sidebar input#q{width:calc(100% - 40px);margin:0 20px 12px;padding:8px 10px;border-radius:6px;
  border:1px solid #2c4258;background:#0d1a26;color:#e8f1fb;font-size:13px}
.toc-root{list-style:none;margin:0;padding:0 8px}
.toc-root ul{list-style:none;margin:2px 0 6px 0;padding:0}
.toc-root li.toc-l2{margin-top:10px}
.toc-root li.toc-l2>a{display:block;padding:6px 12px;font-weight:700;color:#fff;border-radius:6px}
.toc-root li.toc-l2>a:hover{background:#1c3348;text-decoration:none}
.toc-root li.toc-l3>a{display:block;padding:4px 12px 4px 22px;font-size:13.5px;color:#a9c4dc;
  border-left:2px solid transparent;border-radius:0 6px 6px 0}
.toc-root li.toc-l3>a:hover{background:#1c3348;color:#fff;border-left-color:var(--accent);text-decoration:none}
.toc-root li.toc-l3.hide,.toc-root li.toc-l2.hide{display:none}

.main{flex:1 1 auto;min-width:0;padding:0}
.masthead{background:linear-gradient(135deg,#0b3a63,#0b5cab);color:#fff;padding:40px 56px 32px}
.masthead h1{margin:0 0 8px;font-size:26px}
.masthead .meta{color:#cfe3f6;font-size:13.5px}
.legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:18px}
.legend .item{background:rgba(255,255,255,.12);border-radius:8px;padding:6px 12px;font-size:13px}

.content{max-width:920px;margin:0 auto;padding:40px 56px 120px;overflow-x:auto}

h2{font-size:22px;margin:56px 0 18px;padding-top:20px;border-top:3px solid var(--accent);
  scroll-margin-top:20px}
.content>h2:first-of-type{margin-top:0;border-top:none;padding-top:0}
h3{font-size:18px;margin:40px 0 14px;padding:14px 18px;background:var(--accent-soft);
  border-left:4px solid var(--accent);border-radius:6px;scroll-margin-top:20px}
h4{font-size:15px;margin:26px 0 8px;color:var(--accent);border-bottom:1px dashed var(--line);
  padding-bottom:4px}
.secno{display:inline-block;background:var(--accent);color:#fff;font-weight:700;
  font-size:.82em;padding:2px 9px;border-radius:5px;margin-right:9px;font-variant-numeric:tabular-nums}
h2 .secno{background:#0b3a63}

p{margin:10px 0}
hr{border:none;border-top:1px solid var(--line);margin:36px 0}
blockquote{margin:16px 0;padding:12px 18px;background:#fffaf0;border-left:4px solid var(--partial);
  border-radius:4px;color:#5b4a2a}
code{background:var(--code-bg);padding:1px 6px;border-radius:4px;font-size:.92em;
  font-family:Consolas,"D2Coding",monospace}
pre{background:#0d1a26;color:#dfe9f2;padding:16px 18px;border-radius:8px;overflow-x:auto}
pre code{background:none;padding:0;color:inherit}

table{border-collapse:collapse;width:100%;margin:16px 0;font-size:14px}
th,td{border:1px solid var(--line);padding:8px 12px;text-align:left;vertical-align:top}
thead th{background:#eef3f8;font-weight:700}
tbody tr:nth-child(even){background:#fafbfc}
div.tablewrap{overflow-x:auto}

figure.shot{margin:20px 0;text-align:center}
figure.shot img{max-width:100%;border:1px solid var(--line);border-radius:8px;
  box-shadow:0 4px 18px rgba(20,40,60,.12);background:#fff;padding:4px}
figure.shot figcaption{margin-top:8px;font-size:13px;color:var(--muted)}

.stbadge{display:inline-block;border-radius:5px;padding:0 6px;font-weight:600}
.st-done{color:var(--done);background:var(--done-bg)}
.st-partial{color:var(--partial);background:var(--partial-bg)}
.st-todo{color:var(--todo);background:var(--todo-bg)}
.st-wontdo{color:var(--wontdo);background:var(--wontdo-bg)}

.backtop{position:fixed;right:28px;bottom:28px;background:var(--accent);color:#fff;
  border-radius:50%;width:44px;height:44px;display:flex;align-items:center;justify-content:center;
  box-shadow:0 4px 14px rgba(0,0,0,.25);font-size:18px}

@media (max-width: 980px){
  .sidebar{position:fixed;left:-320px;z-index:50;transition:left .2s}
  .sidebar.open{left:0}
  .main{margin-left:0}
  .masthead,.content{padding-left:20px;padding-right:20px}
}
@media print{
  .sidebar,.backtop{display:none}
  .main{width:100%}
  h2{page-break-before:always;border-top:none}
  .content>h2:first-of-type{page-break-before:auto}
  a{color:inherit;text-decoration:none}
}
"""

JS = """
document.addEventListener('DOMContentLoaded',function(){
  var q = document.getElementById('q');
  if(q){
    q.addEventListener('input',function(){
      var v = q.value.trim().toLowerCase();
      document.querySelectorAll('.toc-root li.toc-l2, .toc-root li.toc-l3').forEach(function(li){
        var t = li.querySelector('a').textContent.toLowerCase();
        var show = !v || t.indexOf(v) !== -1;
        li.classList.toggle('hide', !show);
      });
    });
  }
  document.querySelectorAll('table').forEach(function(t){
    var wrap = document.createElement('div');
    wrap.className = 'tablewrap';
    t.parentNode.insertBefore(wrap, t);
    wrap.appendChild(t);
  });
});
"""

html = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>QA 검증 관리 시스템 — 요구사항 및 기능사양서</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{CSS}</style>
</head>
<body>
<div class="layout">
  <nav class="sidebar" id="sidebar">
    <div class="brand">
      <div class="t1">QA 검증 관리 시스템</div>
      <div class="t2">요구사항 · 기능사양서</div>
    </div>
    <input id="q" type="search" placeholder="화면/항목 검색...">
    {sidebar_html}
  </nav>
  <div class="main">
    <header class="masthead">
      <h1>QA 검증 관리 시스템 — 요구사항 및 기능사양서</h1>
      <div class="meta">작성 기준: 2026-09-09 · 운영 서버 10.13.0.222 반영 완료분 · 사양번호 체계: 대분류-중분류 (예: 01-03)</div>
      <div class="legend">
        <span class="item"><span class="stbadge st-done">✅</span> 구현·운영중</span>
        <span class="item"><span class="stbadge st-partial">◐</span> 일부 구현</span>
        <span class="item"><span class="stbadge st-todo">○</span> 미구현</span>
        <span class="item"><span class="stbadge st-wontdo">✋</span> 의도적으로 하지 않음</span>
      </div>
    </header>
    <article class="content">
{body_html}
    </article>
  </div>
</div>
<a class="backtop" href="#top" title="맨 위로">↑</a>
<script>{JS}</script>
</body>
</html>
"""

OUT.write_text(html, encoding="utf-8")
print(f"Written: {OUT} ({len(html)} chars)")
