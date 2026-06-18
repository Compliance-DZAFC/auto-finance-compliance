#!/usr/bin/env python3
"""
批量抓取上海金融监管局行政处罚数据
输出：data/penalties.json
"""
import os
import json
import re
import time
from playwright.sync_api import sync_playwright

BASE_URL = "https://www.nfra.gov.cn/branch/shanghai"
LIST_URL = f"{BASE_URL}/view/pages/common/ItemList.html"
DETAIL_URL = f"{BASE_URL}/view/pages/common/ItemDetail.html"

TARGET_YEAR = "2026"
DATA_FILE = "data/penalties.json"
DEBUG_DIR = "debug"
MAX_PAGES = 10


def ensure_debug_dir():
    os.makedirs(DEBUG_DIR, exist_ok=True)


def save_debug(page, name, suffix=""):
    ensure_debug_dir()
    try:
        html = page.content()
        with open(f"{DEBUG_DIR}/{name}{suffix}.html", "w", encoding="utf-8") as f:
            f.write(html)
        page.screenshot(path=f"{DEBUG_DIR}/{name}{suffix}.png", full_page=True)
    except Exception as e:
        print(f"[DEBUG] 保存失败: {e}")


def extract_doc_no_from_title(page):
    """从页面标题提取文号"""
    for sel in ["h1", "h2", "h3", ".title", ".article-title", "#title", ".detail-title", ".con-title", ".tit", ".wenzhang-title"]:
        el = page.query_selector(sel)
        if el:
            text = el.inner_text().strip()
            m = re.search(r'[（(](.*?罚决字[〔（(]\d{4}[）)〕].*?号)[）)]', text)
            if m:
                return m.group(1).strip()
            m = re.search(r'[沪津京粤浙苏鲁川渝].*?罚决字[〔（(]\d{4}[）)〕].*?号', text)
            if m:
                return m.group(0).strip()
    return ""




def extract_publish_date(page):
    """从页面提取发布日期"""
    text = page.evaluate("() => document.body.innerText")
    m = re.search(r'发布时间[：:]\s*(\d{4}-\d{2}-\d{2})', text)
    if m:
        return m.group(1)
    return ""

def extract_numbers_from_doc_no(doc_no):
    """从文号中提取所有数字，支持合并文号如 50-55, 17-20,22、23"""
    numbers = []
    m = re.search(r'〔\d{4}〕(.+?)号', doc_no)
    if not m:
        return numbers
    
    text = m.group(1)
    parts = re.split(r'[，,、]', text)
    for part in parts:
        part = part.strip()
        if '-' in part or '—' in part or '～' in part:
            range_match = re.match(r'(\d+)\s*[-—～]\s*(\d+)', part)
            if range_match:
                start, end = int(range_match.group(1)), int(range_match.group(2))
                numbers.extend(range(start, end + 1))
        else:
            num_match = re.match(r'(\d+)', part)
            if num_match:
                numbers.append(int(num_match.group(1)))
    return numbers


def parse_merged_penalty(penalty_text, party, doc_no, violation, authority, doc_id, seq, source_url, publish_date):
    """解析合并处罚内容，拆分为多条记录"""
    records = []
    if not penalty_text or not penalty_text.strip():
        records.append({
            "doc_id": doc_id,
            "seq": seq,
            "party": party,
            "doc_no": doc_no,
            "violation": violation,
            "penalty": penalty_text,
            "authority": authority,
            "source_url": source_url,
            "publish_date": publish_date,
        })
        return records

    text = penalty_text.strip()
    sentences = [s.strip() for s in re.split(r'[。；;]', text) if s.strip()]

    for sentence in sentences:
        # 改进正则："分别"不应被包含在人名中
        m = re.search(r'对(.+?)(?:分别)?(?:罚款|给予警告|警告|罚没|责令改正|没收|取消|撤销|禁止)', sentence)
        if not m:
            continue

        persons_str = m.group(1).strip()
        # 去掉末尾可能的"分别"
        persons_str = re.sub(r'分别$', '', persons_str).strip()
        persons = [p.strip() for p in re.split(r'[、，,]', persons_str) if p.strip()]

        idx = sentence.find(persons_str)
        penalty_part = sentence[idx + len(persons_str):].strip()
        if penalty_part.startswith("分别"):
            penalty_part = penalty_part[2:].strip()
        if penalty_part.startswith("，") or penalty_part.startswith("。"):
            penalty_part = penalty_part[1:].strip()

        for person in persons:
            if len(person) < 2 or person in ("及相关责任人员", "及相关人员", "等"):
                continue

            full_penalty = f"对{person}{penalty_part}" if penalty_part else sentence
            records.append({
                "doc_id": doc_id,
                "seq": seq,
                "party": person,
                "doc_no": doc_no,
                "violation": violation,
                "penalty": full_penalty,
                "authority": authority,
                "source_url": source_url,
            })

    if not records:
        records.append({
            "doc_id": doc_id,
            "seq": seq,
            "party": party,
            "doc_no": doc_no,
            "violation": violation,
            "penalty": penalty_text,
            "authority": authority,
            "source_url": source_url,
            "publish_date": publish_date,
        })

    return records

def fetch_list_page(page, page_num=1):
    list_url = (
        f"{LIST_URL}?itemPId=996&itemId=1000"
        f"&itemUrl=ItemListRightList.html"
        f"&itemName=%E8%A1%8C%E6%94%BF%E5%A4%84%E7%BD%9A&page={page_num}"
    )

    print(f"\n{'='*50}")
    print(f"[INFO] 抓取列表页第 {page_num} 页: {list_url}")

    try:
        page.goto(list_url, wait_until="networkidle", timeout=120000)
        page.wait_for_timeout(5000)
    except Exception as e:
        print(f"[WARN] 列表页第 {page_num} 页加载异常: {e}")
        save_debug(page, f"list_page_{page_num}", "_error")
        return []

    save_debug(page, f"list_page_{page_num}")

    doc_ids = page.evaluate("""
        () => {
            const seen = new Set();
            const result = [];
            
            document.querySelectorAll('a').forEach(a => {
                const href = a.getAttribute('href') || '';
                const onclick = a.getAttribute('onclick') || '';
                const text = href + ' ' + onclick;
                
                const matches = text.match(/docId[=:]\\s*(\\d{6,})/g);
                if (matches) {
                    matches.forEach(m => {
                        const id = m.replace(/docId[=:]\\s*/, '');
                        if (!seen.has(id)) {
                            seen.add(id);
                            result.push(id);
                        }
                    });
                }
            });
            
            return result;
        }
    """)

    print(f"[INFO] 第 {page_num} 页找到 {len(doc_ids)} 个 docId: {doc_ids}")
    return doc_ids


def get_total_pages(page):
    try:
        page_num_input = page.query_selector("input#pageNum")
        if page_num_input:
            val = page_num_input.get_attribute("value")
            if val:
                return int(val)
    except Exception:
        pass

    try:
        text = page.evaluate("() => document.body.innerText")
        m = re.search(r'共\s*(\d+)\s*页', text)
        if m:
            return int(m.group(1))
    except Exception:
        pass

    return MAX_PAGES


def fetch_detail(page, doc_id):
    """返回 (records, should_stop) 元组，should_stop=True 表示遇到非目标年份应停止后续抓取"""
    detail_url = f"{DETAIL_URL}?docId={doc_id}&itemId=1000"
    records = []

    try:
        print(f"[INFO] 访问详情页 docId={doc_id}...")
        page.goto(detail_url, wait_until="networkidle", timeout=120000)
        page.wait_for_timeout(3000)

        publish_date = extract_publish_date(page)
        print(f"[INFO] docId={doc_id} 发布日期: {publish_date}")

        doc_no_from_title = extract_doc_no_from_title(page)
        if doc_no_from_title:
            print(f"[INFO] docId={doc_id} 标题文号: {doc_no_from_title}")

        # 关键改进：用跨行状态矩阵正确处理 rowspan，确保数据行列数 = 表头列数
        tables_data = page.evaluate("""
            () => {
                const tables = document.querySelectorAll('table');
                const result = [];
                
                tables.forEach(table => {
                    const allRows = Array.from(table.rows);
                    if (allRows.length < 2) return;
                    
                    // 找到表头行（含"序号"和"当事人"）
                    let headerRowIndex = -1;
                    for (let i = 0; i < Math.min(allRows.length, 3); i++) {
                        const text = allRows[i].innerText.trim().replace(/\\s+/g, '');
                        if (text.includes('序号') && (text.includes('当事人') || text.includes('名称'))) {
                            headerRowIndex = i;
                            break;
                        }
                    }
                    if (headerRowIndex === -1) return;
                    
                    // 提取表头（处理表头自身的 rowspan/colspan）
                    const headerCells = Array.from(allRows[headerRowIndex].cells);
                    const headers = [];
                    let hIdx = 0;
                    for (const cell of headerCells) {
                        const text = cell.innerText.trim().replace(/\\s+/g, '');
                        const colspan = parseInt(cell.getAttribute('colspan') || '1');
                        for (let c = 0; c < colspan; c++) {
                            headers.push(text);
                        }
                    }
                    const colCount = headers.length;
                    
                    // 处理数据行，维护跨行状态
                    const data = [];
                    let rowspanState = new Array(colCount).fill(0); // 每列剩余 rowspan 行数
                    let rowspanValues = new Array(colCount).fill(''); // 每列跨行的值
                    let rowspanGroupMap = new Array(colCount).fill(0); // 每列当前所属的 rowspan 组 ID
                    let nextRowspanGroupId = 1;

                    for (let i = headerRowIndex + 1; i < allRows.length; i++) {
                        const row = allRows[i];
                        const cells = Array.from(row.cells);
                        const rowData = new Array(colCount).fill('');
                        let colIndex = 0;
                        let rowGroupId = 0; // 本行所属的 rowspan 组（0 表示无）

                        for (const cell of cells) {
                            const text = cell.innerText.trim();
                            const colspan = parseInt(cell.getAttribute('colspan') || '1');
                            const rowspan = parseInt(cell.getAttribute('rowspan') || '1');

                            // 跳过被上一行 rowspan 占用的位置，并回填值
                            while (colIndex < colCount && rowspanState[colIndex] > 0) {
                                rowData[colIndex] = rowspanValues[colIndex];
                                if (rowspanGroupMap[colIndex] > 0) {
                                    rowGroupId = rowspanGroupMap[colIndex];
                                }
                                rowspanState[colIndex]--;
                                colIndex++;
                            }
                            if (colIndex >= colCount) break;

                            // 填充当前 cell
                            for (let c = 0; c < colspan; c++) {
                                if (colIndex + c < colCount) {
                                    rowData[colIndex + c] = text;
                                    if (rowspan > 1) {
                                        rowspanState[colIndex + c] = rowspan - 1;
                                        rowspanValues[colIndex + c] = text;
                                        rowspanGroupMap[colIndex + c] = nextRowspanGroupId;
                                        rowGroupId = nextRowspanGroupId;
                                    }
                                }
                            }
                            if (rowspan > 1) nextRowspanGroupId++;
                            colIndex += colspan;
                        }

                        // 处理行尾剩余的 rowspan
                        while (colIndex < colCount) {
                            if (rowspanState[colIndex] > 0) {
                                rowData[colIndex] = rowspanValues[colIndex];
                                if (rowspanGroupMap[colIndex] > 0) {
                                    rowGroupId = rowspanGroupMap[colIndex];
                                }
                                rowspanState[colIndex]--;
                            }
                            colIndex++;
                        }

                        // 将 rowspan 组 ID 作为额外列附加（用于 Python 端识别）
                        rowData.push(rowGroupId);
                        data.push(rowData);
                    }
                    
                    result.push({ headers, data });
                });
                
                return result;
            }
        """)

        found_table = False
        for table in tables_data:
            headers = table['headers']
            data_rows = table['data']
            header_text = "".join(headers)
            col_count = len(headers)

            # 识别处罚表
            has_seq = "序号" in header_text
            has_party = "当事人" in header_text or "名称" in header_text
            
            if not (has_seq and has_party):
                continue

            found_table = True
            print(f"[INFO] docId={doc_id} 识别到处罚表，列数={col_count}，表头={headers}")

            # 判断是否有独立的文号列
            has_doc_no_col = any("文号" in h and "决定" in h for h in headers)
            print(f"[INFO] docId={doc_id} 表格{'有' if has_doc_no_col else '无'}独立文号列")

            for row in data_rows:
                # 读取 rowspan 组标记（JS 端追加的额外列，从末尾提取）
                rowspan_group = 0
                if len(row) == col_count + 1:
                    try:
                        rowspan_group = int(row.pop()) if row[-1] else 0
                    except (ValueError, TypeError):
                        rowspan_group = 0
                elif len(row) > col_count + 1:
                    print(f"[WARN] 行长度不匹配: {len(row)} > {col_count + 1}, row={row}, 跳过")
                    continue
                elif len(row) < col_count:
                    print(f"[WARN] 行长度不匹配: {len(row)} < {col_count}, row={row}, 跳过")
                    continue

                # 动态列映射
                seq = ""
                party = ""
                doc_no = ""
                violation = ""
                penalty = ""
                authority = "上海金融监管局"

                for i, h in enumerate(headers):
                    if i >= len(row):
                        continue
                    h = h.lower()
                    
                    if "序号" in h:
                        seq = row[i]
                    elif ("当事人" in h or "名称" in h) and "机关" not in h:
                        party = row[i]
                    elif "文号" in h and "决定" in h:
                        doc_no = row[i]
                    elif ("违规" in h or "违法" in h or "行为" in h) and "处罚" not in h:
                        violation = row[i]
                    elif ("处罚" in h or "决定" in h) and "机关" not in h and "文号" not in h:
                        penalty = row[i]
                    elif "机关" in h:
                        authority = row[i]

                # 关键修复：如果表格里有文号列但值为空，尝试从整行文本提取
                if has_doc_no_col and not doc_no:
                    for cell_text in row:
                        m = re.search(r'[沪津京粤浙苏鲁川渝].*?罚决字[〔（(]\d{4}[）)〕].*?号', cell_text)
                        if m:
                            doc_no = m.group(0)
                            break

                # 只有表格里确实没有文号列时，才用标题文号
                if not doc_no and not has_doc_no_col and doc_no_from_title:
                    doc_no = doc_no_from_title

                # 兜底赋值
                if not penalty and len(row) >= 2:
                    penalty = row[-2] if len(row) >= 4 else row[-1]
                if not violation and len(row) >= 3:
                    violation = row[2] if len(row) >= 5 else row[1]
                if not party and len(row) >= 2:
                    party = row[1]

                # 判断是否是合并处罚表
                is_merged = not has_doc_no_col and penalty and ("对" in penalty) and any(k in penalty for k in ["罚款", "警告", "禁止", "没收"])

                if is_merged:
                    sub_records = parse_merged_penalty(
                        penalty, party, doc_no, violation, authority,
                        doc_id, seq, detail_url, publish_date
                    )
                    # 将 rowspan_group 传递给子记录
                    for sr in sub_records:
                        sr["rowspan_group"] = rowspan_group
                    records.extend(sub_records)
                else:
                    records.append({
                        "doc_id": doc_id,
                        "seq": seq,
                        "party": party,
                        "doc_no": doc_no,
                        "violation": violation,
                        "penalty": penalty,
                        "authority": authority,
                        "source_url": detail_url,
                        "rowspan_group": rowspan_group,
                        "publish_date": publish_date,
                    })

            if records:
                break

        if not found_table:
            print(f"[WARN] docId={doc_id} 未找到可识别的处罚表格")
            save_debug(page, f"detail_{doc_id}", "_no_table")
        else:
            print(f"[INFO] docId={doc_id} 提取 {len(records)} 条记录")

    except Exception as e:
        print(f"[ERROR] 详情页 docId={doc_id} 抓取失败: {e}")
        save_debug(page, f"detail_{doc_id}", "_error")

    # 智能停止：遇到非目标年份，后续无需继续抓取（列表按时间倒序）
    if publish_date and not publish_date.startswith(TARGET_YEAR):
        print(f"[INFO] docId={doc_id} 发布日期 {publish_date} 非目标年份，触发停止信号")
        return records, True

    return records, False


def filter_by_year(records, year):
    filtered = []
    for r in records:
        text = r.get("doc_no", "") + r.get("penalty", "") + r.get("authority", "") + r.get("source_url", "")
        if year in text:
            filtered.append(r)
    return filtered


def deduplicate_records(records):
    seen = set()
    unique = []
    for r in records:
        key = (r.get("doc_id"), r.get("seq"), r.get("party"))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def check_missing_numbers(records):
    all_numbers = set()
    for r in records:
        doc_no = r.get("doc_no", "")
        if not doc_no:
            continue
        nums = extract_numbers_from_doc_no(doc_no)
        for n in nums:
            if 1 <= n <= 100:
                all_numbers.add(n)
    
    if not all_numbers:
        return []
    
    all_numbers = sorted(all_numbers)
    all_expected = list(range(1, max(all_numbers + [55]) + 1))
    return [i for i in all_expected if i not in all_numbers]


def main():
    os.makedirs("data", exist_ok=True)
    ensure_debug_dir()

    all_records = []
    seen_doc_ids = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        first_ids = fetch_list_page(page, 1)
        for doc_id in first_ids:
            seen_doc_ids.add(doc_id)
            records, should_stop = fetch_detail(page, doc_id)
            if records:
                all_records.extend(records)
            if should_stop:
                print(f"[INFO] 遇到非目标年份记录，停止抓取后续页面")
                break
            time.sleep(1.5)

        total_pages = get_total_pages(page)
        print(f"\n[INFO] 探测到总页数: {total_pages}，开始抓取...")
        total_pages = min(total_pages, MAX_PAGES)

        for page_num in range(2, total_pages + 1):
            doc_ids = fetch_list_page(page, page_num)
            if not doc_ids:
                print(f"[INFO] 第 {page_num} 页无数据，停止")
                break

            new_count = 0
            should_stop_all = False
            for doc_id in doc_ids:
                if doc_id in seen_doc_ids:
                    print(f"[SKIP] docId={doc_id} 已抓取，跳过")
                    continue
                seen_doc_ids.add(doc_id)
                new_count += 1

                records, should_stop = fetch_detail(page, doc_id)
                if records:
                    all_records.extend(records)
                if should_stop:
                    should_stop_all = True
                    print(f"[INFO] 遇到非目标年份记录，停止抓取")
                    break
                time.sleep(1.5)

            print(f"[INFO] 第 {page_num} 页新抓取 {new_count} 个详情页")
            if new_count == 0 or should_stop_all:
                break
            time.sleep(2)

        browser.close()

    before = len(all_records)
    all_records = deduplicate_records(all_records)
    if before != len(all_records):
        print(f"\n[INFO] 记录级去重：{before} -> {len(all_records)} 条")

    print(f"\n[INFO] 共抓取 {len(all_records)} 条原始记录，过滤 {TARGET_YEAR} 年...")
    filtered = filter_by_year(all_records, TARGET_YEAR)
    print(f"[INFO] {TARGET_YEAR} 年记录: {len(filtered)} 条")

    missing = check_missing_numbers(filtered)
    if missing:
        print(f"[WARN] 文号缺失: {missing}（共 {len(missing)} 个）")
    else:
        print(f"[INFO] 文号连续，无缺失")

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print(f"[DONE] 数据已保存到 {DATA_FILE}")


if __name__ == "__main__":
    main()