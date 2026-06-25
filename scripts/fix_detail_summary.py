#!/usr/bin/env python3
"""
直接在 dist/index.html 和 docs/index.html 上修复详情面板：
补充完整风险标签和完整主要违规事由。
"""
import json
import re
import html
import os
import shutil

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(BASE_DIR, "scripts", "data", "penalties_enriched.json")
DIST_FILE = os.path.join(BASE_DIR, "dist", "index.html")
DOCS_FILE = os.path.join(BASE_DIR, "docs", "index.html")

NEW_CSS = """
.detail-summary{margin-top:20px;background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.05)}
.detail-summary h4{font-size:14px;font-weight:700;color:#111827;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.detail-summary h4:not(:first-child){margin-top:16px}
.detail-summary .full-tags{display:flex;flex-wrap:wrap;gap:6px}
.detail-summary .full-tags .tag{white-space:normal;line-height:1.4}
.detail-summary .full-violation{font-size:14px;color:#475569;line-height:1.7;margin:0;word-break:break-word}
"""


def load_records():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    by_doc_no = {}
    by_party = {}
    for r in data:
        dn = r.get("doc_no", "")
        by_doc_no.setdefault(dn, []).append(r)
        by_party[r.get("party", "")] = r
    return by_doc_no, by_party


def expand_doc_nos(doc_no_str):
    """把合并文号展开为单一文号列表。"""
    result = []
    # 1-2号 / 5-6号
    for m in re.finditer(r"〔2026〕(\d+)-(\d+)号", doc_no_str):
        prefix = doc_no_str[: m.start()] + "〔2026〕"
        for n in range(int(m.group(1)), int(m.group(2)) + 1):
            result.append(f"沪金罚决字〔2026〕{n}号")
    # 单个文号：4号、7号
    for m in re.finditer(r"〔2026〕(\d+)号", doc_no_str):
        # 如果已经被范围包含，则跳过
        already = False
        for mm in re.finditer(r"〔2026〕(\d+)-(\d+)号", doc_no_str):
            if mm.start() <= m.start() < mm.end():
                already = True
                break
        if already:
            continue
        result.append(f"沪金罚决字〔2026〕{m.group(1)}号")
    return result


def find_records(doc_no, party, by_doc_no, by_party):
    """返回与该 case-group 对应的全部记录。"""
    # 1) 尝试展开合并文号
    doc_nos = expand_doc_nos(doc_no)
    records = []
    for dn in doc_nos:
        records.extend(by_doc_no.get(dn, []))
    if records:
        return records

    # 2) 精确文号
    records = by_doc_no.get(doc_no, [])
    if records:
        return records

    # 3) 按当事人兜底
    if party in by_party:
        return [by_party[party]]

    # 4) 模糊兜底
    for p, rec in by_party.items():
        if p in party or party in p:
            return [rec]
    return []


def merge_summary(records):
    tags = []
    violations = []
    seen_tags = set()
    seen_violations = set()
    for r in records:
        for t in r.get("tags", []):
            if t not in seen_tags:
                seen_tags.add(t)
                tags.append(t)
        v = r.get("violation", "")
        if v and v not in seen_violations:
            seen_violations.add(v)
            violations.append(v)
    return tags, "\n".join(violations)


def build_summary_html(tags, violation):
    tags_html = " ".join(
        f'<span class="tag">{html.escape(str(t))}</span>' for t in tags
    )
    return (
        '<div class="detail-summary">'
        '<h4><span class="section-icon">&#127991;</span>风险标签</h4>'
        f'<div class="full-tags">{tags_html}</div>'
        '<h4><span class="section-icon">&#128220;</span>主要违规事由</h4>'
        f'<p class="full-violation">{html.escape(violation)}</p>'
        "</div>"
    )


def iter_case_groups(html_text):
    """用栈匹配，正确处理嵌套在 detail-parties-table 里的 tbody。"""
    pos = 0
    while True:
        start = html_text.find('<tbody class="case-group"', pos)
        if start == -1:
            break
        depth = 0
        i = start
        while i < len(html_text):
            if html_text.startswith("<tbody", i):
                depth += 1
                i += 6
            elif html_text.startswith("</tbody>", i):
                depth -= 1
                i += 8
                if depth == 0:
                    yield html_text[start:i]
                    pos = i
                    break
            else:
                i += 1
        else:
            break


def process_group(group_html, by_doc_no, by_party):
    if 'class="detail-summary"' in group_html:
        return group_html

    doc_no_match = re.search(
        r'<td class="cell-mono[^"]*"><span class="expand-icon">.*?</span><span>([^<]+)</span></td>',
        group_html,
        re.S,
    )
    party_match = re.search(
        r'<td class="cell-party[^"]*" title="([^"]*)">',
        group_html,
        re.S,
    )
    if not doc_no_match or not party_match:
        return group_html

    doc_no = html.unescape(doc_no_match.group(1)).strip()
    party = html.unescape(party_match.group(1)).strip()

    records = find_records(doc_no, party, by_doc_no, by_party)
    if not records:
        return group_html

    tags, violation = merge_summary(records)
    if not tags and not violation:
        return group_html

    summary_html = build_summary_html(tags, violation)

    # 优先在 detail-source 前插入；如果没有 detail-source，则在 detail-panel 闭合前插入
    if '<a class="detail-source"' in group_html:
        return re.sub(
            r'(\s*)(<a class="detail-source")',
            r'\1' + summary_html + r'\1\2',
            group_html,
            count=1,
            flags=re.S,
        )
    return re.sub(
        r'(\s*)(</div>\s*</div>\s*</td>\s*</tr>)\s*$',
        r'\1' + summary_html + r'\1\2',
        group_html,
        count=1,
        flags=re.S,
    )


def process_html(html_text, by_doc_no, by_party):
    if ".detail-summary{" not in html_text:
        html_text = html_text.replace("</style>", NEW_CSS + "</style>", 1)

    result = html_text
    offset = 0
    # 用 find 定位每个完整 group 并原地替换，避免正则错配内部 tbody
    pos = 0
    while True:
        start = result.find('<tbody class="case-group"', pos)
        if start == -1:
            break
        depth = 0
        i = start
        while i < len(result):
            if result.startswith("<tbody", i):
                depth += 1
                i += 6
            elif result.startswith("</tbody>", i):
                depth -= 1
                i += 8
                if depth == 0:
                    group_html = result[start:i]
                    new_group = process_group(group_html, by_doc_no, by_party)
                    result = result[:start] + new_group + result[i:]
                    pos = start + len(new_group)
                    break
            else:
                i += 1
        else:
            break
    return result


def main():
    by_doc_no, by_party = load_records()

    with open(DIST_FILE, "r", encoding="utf-8") as f:
        dist_html = f.read()

    new_dist = process_html(dist_html, by_doc_no, by_party)

    with open(DIST_FILE, "w", encoding="utf-8") as f:
        f.write(new_dist)

    shutil.copy2(DIST_FILE, DOCS_FILE)
    print("Updated", DIST_FILE, "and", DOCS_FILE)


if __name__ == "__main__":
    main()
