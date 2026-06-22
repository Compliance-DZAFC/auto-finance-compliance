#!/usr/bin/env python3
"""
网页生成阶段（build）：读取 enrich.py 输出的 penalties_enriched.json，
按 doc_id 分组聚合后生成 dist/index.html。

使用方式：
  python scripts/enrich.py   # 先生成/更新增强数据
  python scripts/build.py    # 再生成网页
"""
import os
import json
import re
from datetime import datetime

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "penalties_enriched.json")
OUTPUT_FILE = "dist/index.html"

# AI 助手配置（与 enrich.py 保持一致）
# 注意：以下 Key 会嵌入前端 JS，仅供本地使用，请勿部署到公网
LLM_API_KEY = os.environ.get("KIMI_API_KEY", "sk-ttACQINTYwQrwKIpPIiIhDJfVkWPrYiLY14Vm1kn8SRAr5nS")
LLM_API_BASE = os.environ.get("KIMI_API_BASE", "https://api.moonshot.cn/v1")
LLM_MODEL = os.environ.get("KIMI_MODEL", "kimi-k2.5")  # 自动使用 Moonshot 最新最强模型


def extract_doc_no_number(doc_no):
    """从文号中提取数字序号"""
    m = re.search(r'〔\d{4}〕(\d+)', doc_no)
    return int(m.group(1)) if m else 0


def merge_doc_nos(doc_nos):
    """合并多个文号为简洁格式，如 1号、2号 -> 1-2号"""
    if len(doc_nos) == 1:
        return doc_nos[0]
    if len(doc_nos) == 0:
        return ""

    # 提取前缀、数字、后缀
    numbers = []
    pattern = re.compile(r'(.*?〔\d{4}〕)(\d+)(号)')
    for d in sorted(doc_nos, key=lambda x: extract_doc_no_number(x)):
        m = pattern.match(d)
        if m:
            numbers.append((m.group(1), int(m.group(2)), m.group(3)))

    if not numbers or len(numbers) != len(doc_nos):
        return "、".join(doc_nos)

    # 检查是否前缀后缀相同且连续
    prefix = numbers[0][0]
    suffix = numbers[0][2]
    if all(n[0] == prefix and n[2] == suffix for n in numbers):
        nums = [n[1] for n in numbers]
        if nums == list(range(nums[0], nums[-1] + 1)):
            return f"{prefix}{nums[0]}-{nums[-1]}{suffix}"

    return "、".join(f"{n[0]}{n[1]}{n[2]}" for n in numbers)


def classify_amount_range(amount):
    """将罚款金额归入区间"""
    if amount == 0:
        return "0（警告等）"
    elif amount < 10:
        return "1-10万"
    elif amount < 50:
        return "10-50万"
    elif amount < 100:
        return "50-100万"
    elif amount < 500:
        return "100-500万"
    else:
        return "500万及以上"


def extract_position(party):
    """从当事人名称中提取职级/身份"""
    if not party:
        return "其他"
    # 机构主体
    if any(k in party for k in ["银行", "公司", "信用社", "合作社", "金融租赁", "财务公司", "消费金融"]):
        return "机构主体"
    # 个人职级（按优先级）
    if "董事长" in party or "理事" in party:
        return "董事长/理事"
    elif "行长" in party:
        return "行长"
    elif "总经理" in party or "总裁" in party:
        return "总经理/总裁"
    elif "经理" in party or "主管" in party:
        return "经理/主管"
    elif "负责人" in party:
        return "负责人"
    elif "员工" in party or "个人" in party:
        return "员工/个人"
    else:
        return "其他个人"


def extract_violation_keywords(violation):
    """提取违规事由关键词（用于TOP事由分析）"""
    keywords = [
        "违规发放贷款", "贷后管理", "贷款三查", "资金流向", "资金挪用",
        "授信管理", "关联交易", "票据业务", "数据治理", "数据不真实",
        "EAST", "投资业务", "理财业务", "公司治理", "内部控制",
        "员工行为", "消费者权益", "反洗钱", "风险分类", "服务收费",
        "信息披露", "固定资产贷款", "流动资金贷款", "个人贷款",
        "房地产", "信用卡", "同业业务", "罚款", "警告"
    ]
    return [k for k in keywords if k in violation]


def group_by_doc_id(data):
    """
    按 (doc_id, rowspan_group) 分组：
    - rowspan_group > 0 表示原始表格中存在 rowspan 合并，这些记录属于同一案件（双罚）
    - rowspan_group == 0 表示独立案件，不与其他记录合并
    """
    # 第一步：按 (doc_id, rowspan_group) 分组
    case_groups = {}
    for r in data:
        did = r.get("doc_id", "")
        rg = r.get("rowspan_group", 0) or 0
        # 只有 rowspan_group > 0 的记录才按组合并；为 0 的每条独立
        if rg > 0:
            key = (did, rg)
        else:
            # 为 0 的用 doc_no 作为唯一键，确保独立成组
            key = (did, f"single_{r.get('doc_no', '')}_{r.get('party', '')}")
        if key not in case_groups:
            case_groups[key] = []
        case_groups[key].append(r)

    result = []
    for idx, (key, records) in enumerate(
        sorted(case_groups.items(), key=lambda x: min(extract_doc_no_number(r["doc_no"]) for r in x[1]))
    ):
        # 组内按文号排序
        records.sort(key=lambda r: extract_doc_no_number(r["doc_no"]))

        # 合并文号（如 1号、2号 -> 1-2号）
        doc_nos = [r["doc_no"] for r in records]
        doc_no_display = merge_doc_nos(doc_nos)

        total_amount = sum(r["amount"] for r in records)
        org_records = [r for r in records if r.get("institution_type") != "个人"]
        main_record = org_records[0] if org_records else records[0]

        sub_item = {
            "doc_no": doc_no_display,
            "main_party": main_record["party"],
            "field": main_record["field"],
            "sub_field": main_record.get("sub_field", ""),
            "violation": main_record["violation"],
            "penalty": main_record.get("penalty", ""),
            "total_amount": total_amount,
            "level": main_record["level"],
            "tags": main_record["tags"],
            "mapping": main_record["mapping"],
            "advice": main_record["advice"],
            "source_url": main_record["source_url"],
            "publish_date": main_record.get("publish_date", ""),
            "category_tags": main_record.get("category_tags", []),
            "records": records,
            "count": len(records)
        }

        min_no = min(extract_doc_no_number(d) for d in doc_nos)

        result.append({
            "doc_id": did,
            "group_idx": idx % 4,
            "min_doc_no": min_no,
            "sub_items": [sub_item],
            "total_amount": total_amount
        })

    # 默认按文号正序
    result.sort(key=lambda x: x["min_doc_no"])
    return result


def build_html(groups, year, raw_count):
    total = sum(len(g["sub_items"]) for g in groups)
    total_amount = sum(g["total_amount"] for g in groups)

    field_stats = {}
    for g in groups:
        for sub in g["sub_items"]:
            f = sub["field"]
            field_stats[f] = field_stats.get(f, {"count": 0, "amount": 0})
            field_stats[f]["count"] += sub["count"]
            field_stats[f]["amount"] += sub["total_amount"]
    field_list = sorted(field_stats.items(), key=lambda x: x[1]["amount"], reverse=True)

    risk_stats = {"高": 0, "中": 0, "低": 0}
    for g in groups:
        for sub in g["sub_items"]:
            risk_stats[sub["level"]] += 1

    # 新增统计维度
    # 1. 机构类型分布（按金额）
    inst_stats = {}
    for g in groups:
        for sub in g["sub_items"]:
            for r in sub["records"]:
                it = r.get("institution_type", "其他")
                if it not in inst_stats:
                    inst_stats[it] = {"count": 0, "amount": 0}
                inst_stats[it]["count"] += 1
                inst_stats[it]["amount"] += r.get("amount", 0)
    inst_list = sorted(inst_stats.items(), key=lambda x: x[1]["amount"], reverse=True)

    # 2. 处罚类型分布
    penalty_type_stats = {}
    for g in groups:
        for sub in g["sub_items"]:
            for r in sub["records"]:
                pt = r.get("penalty_type", "其他")
                if pt not in penalty_type_stats:
                    penalty_type_stats[pt] = {"count": 0, "amount": 0}
                penalty_type_stats[pt]["count"] += 1
                penalty_type_stats[pt]["amount"] += r.get("amount", 0)
    penalty_type_list = sorted(penalty_type_stats.items(), key=lambda x: x[1]["count"], reverse=True)

    # 3. 高额罚单 TOP10
    top_penalties = []
    for g in groups:
        for sub in g["sub_items"]:
            for r in sub["records"]:
                if r.get("amount", 0) > 0:
                    top_penalties.append(r)
    top_penalties.sort(key=lambda x: x["amount"], reverse=True)
    top10 = top_penalties[:10]

    # 4. 双罚统计
    dual_penalty_count = sum(1 for g in groups for sub in g["sub_items"] if sub["count"] > 1)
    single_penalty_count = sum(1 for g in groups for sub in g["sub_items"] if sub["count"] == 1)

    # 5. 个人 vs 机构统计
    person_count = sum(1 for g in groups for sub in g["sub_items"] for r in sub["records"] if r.get("institution_type") == "个人")
    org_count = sum(1 for g in groups for sub in g["sub_items"] for r in sub["records"] if r.get("institution_type") != "个人")

    # 6. 金额区间分布
    amount_range_stats = {}
    for g in groups:
        for sub in g["sub_items"]:
            for r in sub["records"]:
                ar = classify_amount_range(r.get("amount", 0))
                if ar not in amount_range_stats:
                    amount_range_stats[ar] = {"count": 0, "amount": 0}
                amount_range_stats[ar]["count"] += 1
                amount_range_stats[ar]["amount"] += r.get("amount", 0)
    amount_range_order = ["0（警告等）", "1-10万", "10-50万", "50-100万", "100-500万", "500万及以上"]
    amount_range_list = [(k, amount_range_stats.get(k, {"count": 0, "amount": 0})) for k in amount_range_order if k in amount_range_stats]

    # 7. 被处罚对象职级/身份分布
    position_stats = {}
    for g in groups:
        for sub in g["sub_items"]:
            for r in sub["records"]:
                pos = extract_position(r.get("party", ""))
                if pos not in position_stats:
                    position_stats[pos] = {"count": 0, "amount": 0}
                position_stats[pos]["count"] += 1
                position_stats[pos]["amount"] += r.get("amount", 0)
    position_list = sorted(position_stats.items(), key=lambda x: x[1]["amount"], reverse=True)

    # 8. 违规事由关键词 TOP15
    keyword_stats = {}
    for g in groups:
        for sub in g["sub_items"]:
            for r in sub["records"]:
                for kw in extract_violation_keywords(r.get("violation", "")):
                    if kw not in keyword_stats:
                        keyword_stats[kw] = {"count": 0, "amount": 0}
                    keyword_stats[kw]["count"] += 1
                    keyword_stats[kw]["amount"] += r.get("amount", 0)
    keyword_list = sorted(keyword_stats.items(), key=lambda x: x[1]["count"], reverse=True)[:15]

    # 9. 百万级高额罚单统计
    million_plus_records = [r for g in groups for sub in g["sub_items"] for r in sub["records"] if r.get("amount", 0) >= 100]
    million_plus_count = len(million_plus_records)
    million_plus_amount = sum(r.get("amount", 0) for r in million_plus_records)

    # 10. 机构 vs 个人罚款金额
    org_amount = sum(r.get("amount", 0) for g in groups for sub in g["sub_items"] for r in sub["records"] if r.get("institution_type") != "个人")
    person_amount = sum(r.get("amount", 0) for g in groups for sub in g["sub_items"] for r in sub["records"] if r.get("institution_type") == "个人")

    # 11. 二级分类分布（按金额）
    sub_field_stats = {}
    for g in groups:
        for sub in g["sub_items"]:
            sf = sub.get("sub_field", "其他")
            if sf not in sub_field_stats:
                sub_field_stats[sf] = {"count": 0, "amount": 0}
            sub_field_stats[sf]["count"] += sub["count"]
            sub_field_stats[sf]["amount"] += sub["total_amount"]
    sub_field_all = sorted(sub_field_stats.items(), key=lambda x: x[1]["amount"], reverse=True)
    sub_field_list = sub_field_all[:15]

    # 12. 月度处罚趋势（按 publish_date 聚合金额与数量）
    monthly_stats = {}
    for g in groups:
        for sub in g["sub_items"]:
            date_str = sub.get("publish_date", "")
            if date_str and len(date_str) >= 7:
                month = date_str[:7]
            else:
                month = "未知"
            if month not in monthly_stats:
                monthly_stats[month] = {"count": 0, "amount": 0}
            monthly_stats[month]["count"] += sub["count"]
            monthly_stats[month]["amount"] += sub["total_amount"]
    
    # 补全缺失月份：找到最小和最大月份，生成连续月份列表（包括0值的月份如2026-04）
    raw_months = sorted([m for m in monthly_stats.keys() if m != "未知"])
    if raw_months:
        from datetime import datetime
        start = datetime.strptime(raw_months[0], "%Y-%m")
        end = datetime.strptime(raw_months[-1], "%Y-%m")
        cur_year, cur_month = start.year, start.month
        end_year, end_month = end.year, end.month
        while (cur_year, cur_month) <= (end_year, end_month):
            month_key = f"{cur_year}-{cur_month:02d}"
            if month_key not in monthly_stats:
                monthly_stats[month_key] = {"count": 0, "amount": 0}
            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1
    
    monthly_list = sorted(monthly_stats.items(), key=lambda x: x[0])

    # 新增：热力图数据 - 各领域 × 月份案件数
    # 以CSV/Excel标准分类为准：9个一级分类
    all_fields = [
        "公司治理", "监管报告报表", "供应链金融", "信贷业务",
        "柜台业务", "内控合规案防", "消费者保护", "风险管理", "其他业务"
    ]
    
    heatmap_stats = {f: {} for f in all_fields}
    for g in groups:
        for sub in g["sub_items"]:
            date_str = sub.get("publish_date", "")
            if date_str and len(date_str) >= 7:
                month = date_str[:7]
            else:
                month = "未知"
            field = sub.get("field", "其他业务")
            if field not in all_fields:
                field = "其他业务"
            if field not in heatmap_stats:
                heatmap_stats[field] = {}
            if month not in heatmap_stats[field]:
                heatmap_stats[field][month] = 0
            heatmap_stats[field][month] += sub["count"]

    # 排序：按领域总案件数从高到低排序字段
    field_total_counts = {f: sum(m.values()) for f, m in heatmap_stats.items()}
    sorted_fields = sorted(field_total_counts.keys(), key=lambda f: field_total_counts[f], reverse=True)
    
    # 补全月份：找到最小和最大月份，生成连续月份列表（包括空月份如2026-04）
    raw_months = sorted(set(m for fm in heatmap_stats.values() for m in fm.keys() if m != "未知"))
    if raw_months:
        from datetime import datetime
        start = datetime.strptime(raw_months[0], "%Y-%m")
        end = datetime.strptime(raw_months[-1], "%Y-%m")
        all_months = []
        cur_year, cur_month = start.year, start.month
        end_year, end_month = end.year, end.month
        while (cur_year, cur_month) <= (end_year, end_month):
            all_months.append(f"{cur_year}-{cur_month:02d}")
            cur_month += 1
            if cur_month > 12:
                cur_month = 1
                cur_year += 1
    else:
        all_months = []



    heatmap_data = []
    for i, field in enumerate(sorted_fields):
        for j, month in enumerate(all_months):
            count = heatmap_stats.get(field, {}).get(month, 0)
            heatmap_data.append([j, i, count])

    heatmap_data_json = json.dumps(heatmap_data, ensure_ascii=False)
    heatmap_fields_json = json.dumps(sorted_fields, ensure_ascii=False)
    heatmap_months_json = json.dumps(all_months, ensure_ascii=False)

    # 生成 tbody（每个 doc_id 一个 tbody，实现视觉分组）
    tbodies = []
    for g in groups:
        tbody_rows = []
        for sub in g["sub_items"]:
            visible_tags = sub["tags"][:3]
            tags_html = " ".join(['<span class="tag">' + t + '</span>' for t in visible_tags])
            if len(sub["tags"]) > 3:
                tags_html += ' <span class="tag-more">+' + str(len(sub["tags"]) - 3) + '</span>'
            advice = "".join(['<li>' + a + '</li>' for a in sub["advice"]])

            parties_summary = sub["main_party"]
            if sub["count"] > 1:
                parties_summary += ' <span style="color:#9ca3af;font-size:12px">(+' + str(sub["count"]-1) + '人)</span>'

            risk_class = "risk-high" if sub["level"] == "高" else "risk-medium" if sub["level"] == "中" else "risk-low"
            publish_date = sub.get("publish_date", "")
            sub_field = sub.get("sub_field", "")
            category_tags = sub.get("category_tags", [])
            amount_display = ("{:,.0f}".format(sub["total_amount"]) if sub["total_amount"] > 0 else "-") + '<span class="unit">万元</span>'

            row = '<tr class="case-row" onclick="toggleDetail(this)">' \
                + '<td class="cell-mono"><span class="expand-icon">&#9654;</span><span>' + sub["doc_no"] + '</span></td>' \
                + '<td class="cell-date">' + (publish_date if publish_date else "-") + '</td>' \
                + '<td class="cell-party line-clamp-3" title="' + sub["main_party"] + '">' + parties_summary + '</td>' \
                + '<td><span class="badge-field">' + sub["field"] + '</span></td>' \
                + '<td><span class="badge-field">' + (sub_field if sub_field else "-") + '</span></td>' \
                + '<td class="cell-violation line-clamp-3" title="' + sub["violation"] + '">' + sub["violation"] + '</td>' \
                + '<td class="cell-penalty line-clamp-2" title="' + sub.get("penalty", "") + '">' + sub.get("penalty", "") + '</td>' \
                + '<td class="cell-amount">' + amount_display + '</td>' \
                + '<td><span class="risk-badge ' + risk_class + '">' + sub["level"] + '风险</span></td>' \
                + '<td>' + tags_html + '</td>' \
                + '</tr>'

            category_tags_html = ""
            if category_tags:
                category_tags_html = '<div class="category-tags"><h5>&#127991; 其他命中分类</h5>' \
                    + "".join(['<span class="tag">' + t + '</span>' for t in category_tags]) + '</div>'

            parties_detail_html = ""
            if sub["count"] > 1:
                parties_rows = ""
                for r in sub["records"]:
                    parties_rows += '<tr>' \
                        + '<td>' + r["party"] + '</td>' \
                        + '<td>' + r["penalty"] + '</td>' \
                        + '<td>' + ("{:,.0f}".format(r["amount"]) + '<span style="font-size:11px;color:#9ca3af">万元</span>' if r["amount"] > 0 else '<span style="color:#9ca3af">-</span>') + '</td></tr>'
                parties_detail_html = '<div class="detail-parties"><h4>&#128221; 处罚当事人明细（共' + str(sub["count"]) + '条）</h4>' \
                    + '<table class="detail-parties-table"><thead><tr>' \
                    + '<th>当事人</th><th>处罚内容</th><th style="text-align:right">金额</th></tr></thead>' \
                    + '<tbody>' + parties_rows + '</tbody></table></div>'

            detail_inner = '<div class="detail-panel ' + risk_class + '">' \
                + '<div class="detail-header">' \
                + '<div>' \
                + '<div class="detail-title">' + sub["doc_no"] + ' · ' + sub["main_party"] + '</div>' \
                + '<div class="detail-meta">发布时间：' + (publish_date if publish_date else "-") + '　|　罚款金额：' + amount_display + '　|　处罚内容：' + sub.get("penalty", "-") + '</div>' \
                + '</div>' \
                + '<span class="risk-badge ' + risk_class + '">' + sub["level"] + '风险</span>' \
                + '</div>' \
                + '<div class="detail-grid">' \
                + '<div class="detail-section"><h4><span class="section-icon">&#128663;</span>汽车金融映射风险</h4><p>' + sub["mapping"] + '</p>' + category_tags_html + '</div>' \
                + '<div class="detail-section"><h4><span class="section-icon">&#9989;</span>合规管控建议</h4><ul>' + advice + '</ul></div>' \
                + '</div>' \
                + parties_detail_html \
                + '<a class="detail-source" href="' + sub["source_url"] + '" target="_blank">查看原文 &#8599;</a>' \
                + '</div>'

            detail_row = '<tr class="detail-row hidden"><td colspan="10" style="padding:0">' + detail_inner + '</td></tr>'

            tbody_rows.append(row)
            tbody_rows.append(detail_row)

        tbody = '<tbody class="case-group" data-doc-id="' + g["doc_id"] + '" data-group-idx="' + str(g["group_idx"]) + '" data-sort-no="' + str(g["min_doc_no"]) + '" data-sort-amount="' + str(g["total_amount"]) + '">' \
            + "\n".join(tbody_rows) + '</tbody>'
        tbodies.append(tbody)

    tbodies_html = "\n".join(tbodies)

    field_chart = json.dumps([{"name": k, "value": round(v["amount"], 2)} for k, v in field_list[:10]], ensure_ascii=False)
    risk_chart = json.dumps([{"name": k, "value": v} for k, v in risk_stats.items()], ensure_ascii=False)
    field_options = "".join(['<option value="' + k + '">' + k + '</option>' for k, _ in field_list])
    sub_field_options = "".join(['<option value="' + k + '">' + k + '</option>' for k, _ in sub_field_all])

    # 新增图表数据
    amount_range_chart = json.dumps([{"name": k, "amount": round(v["amount"], 2), "count": v["count"]} for k, v in amount_range_list], ensure_ascii=False)
    position_chart = json.dumps([{"name": k, "amount": round(v["amount"], 2), "count": v["count"]} for k, v in position_list], ensure_ascii=False)
    keyword_chart = json.dumps([{"name": k, "value": v["count"], "amount": round(v["amount"], 2)} for k, v in keyword_list], ensure_ascii=False)
    dual_chart = json.dumps([{"name": "单罚", "value": single_penalty_count}, {"name": "双罚", "value": dual_penalty_count}], ensure_ascii=False)
    person_org_amount_chart = json.dumps([{"name": "机构罚款", "value": round(org_amount, 2)}, {"name": "个人罚款", "value": round(person_amount, 2)}], ensure_ascii=False)
    inst_dual_chart = json.dumps([{"name": k, "amount": round(v["amount"], 2), "count": v["count"]} for k, v in inst_list], ensure_ascii=False)
    penalty_type_dual_chart = json.dumps([{"name": k, "amount": round(v["amount"], 2), "count": v["count"]} for k, v in penalty_type_list], ensure_ascii=False)
    sub_field_chart = json.dumps([{"name": k, "amount": round(v["amount"], 2), "count": v["count"]} for k, v in sub_field_list], ensure_ascii=False)
    monthly_chart = json.dumps([{"month": k, "amount": round(v["amount"], 2), "count": v["count"]} for k, v in monthly_list], ensure_ascii=False)

    # AI 助手：构建精简案例摘要作为系统提示词上下文，控制 token 消耗
    case_summary = []
    for g in groups:
        for sub in g["sub_items"]:
            case_summary.append({
                "doc_no": sub["doc_no"],
                "party": sub["main_party"],
                "field": sub["field"],
                "sub_field": sub.get("sub_field", ""),
                "violation": sub["violation"],
                "amount": sub["total_amount"],
                "level": sub["level"],
                "advice": sub["advice"],
            })
    case_summary_json = json.dumps(case_summary, ensure_ascii=False)
    print(f"[AI] 案例摘要 token 估算：约 {len(case_summary_json)} 字符（中文约 {len(case_summary_json)//2} tokens）")

    system_prompt = (
        "你是一位资深的汽车金融合规分析专家，基于国家金融监督管理总局上海监管局的处罚数据为用户答疑解惑。\n"
        f"当前数据集为{year}年，共{total}条处罚案例，罚款总金额{total_amount:.2f}万元。\n"
        "\n"
        "【回答规则】\n"
        "1. 回答必须基于下方提供的案例数据，不得编造不存在的事实。\n"
        "2. 先直接给出核心结论，再补充细节和分析。\n"
        "3. 涉及具体案例时，引用处罚文号和当事人名称，让用户可以追溯。\n"
        "4. 提供合规建议时，要结合汽车金融公司的实际业务场景，给出可操作的具体措施。\n"
        "5. 如果用户问的是数据中没有覆盖的问题，坦诚说明并给出一般性行业建议。\n"
        "6. 使用专业但易懂的语言，避免过度学术化。\n"
        "\n"
        "【回答格式】\n"
        "- 用户问趋势/统计：先用数字总结，再分点说明原因\n"
        "- 用户问具体案例：先说明案例概况，再分析违规点和处罚结果，最后给出映射建议\n"
        "- 用户问合规建议：分点列出，每条建议标注优先级（高/中/低）\n"
    )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    now_full = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">' \
        + '<meta name="viewport" content="width=device-width, initial-scale=1.0">' \
        + '<title>汽车金融合规看板 · 上海局 ' + year + '</title>' \
        + '<script src="https://cdn.tailwindcss.com"></script>' \
        + '<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>' \
        + '<style>' \
        + 'body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans SC",sans-serif;background:#f8f9fa;margin:0;color:#111827}' \
        + '.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08),0 4px 12px rgba(0,0,0,.05)}' \
        + '.kpi{transition:transform .2s,box-shadow .2s}' \
        + '.kpi:hover{transform:translateY(-2px);box-shadow:0 8px 24px rgba(0,0,0,.09)}' \
        + 'table{width:100%;border-collapse:collapse;table-layout:fixed}' \
        + '.case-group{border-top:1px solid #e2e8f0}' \
        + '.case-group:first-child{border-top:none}' \
        + '.case-row{transition:background-color .15s}' \
        + '.case-row:hover{background:#f1f5f9;cursor:pointer}' \
        + '.case-row.expanded{background:#f5f3fa}' \
        + '.case-row td{padding:10px 12px;border-bottom:1px solid #f3f4f6;vertical-align:middle;font-size:13px}' \
        + '.detail-row{display:none}' \
        + '.detail-row:not(.hidden){display:table-row;animation:fadeIn .25s ease-out}' \
        + '.hidden{display:none !important}' \
        + '.expand-icon{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:6px;background:#f1f5f9;color:#64748b;font-size:10px;transition:transform .2s;vertical-align:middle;margin-right:6px;flex-shrink:0}' \
        + '.case-row.expanded .expand-icon{transform:rotate(90deg);background:#e8e0f0;color:#5c2a7f}' \
        + '.tag{display:inline-block;padding:2px 6px;border-radius:4px;background:#f1f5f9;color:#475569;font-size:11px;margin-right:3px;margin-bottom:2px;white-space:nowrap}' \
        + '.tag-more{display:inline-block;padding:2px 6px;border-radius:4px;background:#e2e8f0;color:#64748b;font-size:11px;font-weight:500;white-space:nowrap}' \
        + '.badge-field{display:inline-block;padding:2px 6px;border-radius:4px;background:#e5e7eb;color:#374151;font-size:11px;font-weight:500}' \
        + '.risk-badge{display:inline-flex;align-items:center;gap:3px;padding:3px 8px;border-radius:20px;font-size:11px;font-weight:600}' \
        + '.risk-high{background:#fee2e2;color:#991b1b}' \
        + '.risk-medium{background:#fef3c7;color:#92400e}' \
        + '.risk-low{background:#d1fae5;color:#065f46}' \
        + '.cell-mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#6b7280;line-height:1.5;word-break:break-all}' \
        + '.cell-party{font-weight:600;color:#111827;font-size:13px;line-height:1.5}' \
        + '.cell-violation{color:#374151;font-size:13px;line-height:1.5}' \
        + '.cell-penalty{color:#374151;font-size:13px;line-height:1.5}' \
        + '.cell-amount{text-align:right;font-weight:700;color:#111827;font-size:13px;white-space:nowrap}' \
        + '.cell-amount .unit{font-size:11px;color:#9ca3af;font-weight:400;margin-left:2px}' \
        + '.cell-date{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#6b7280;white-space:nowrap;line-height:1.5}' \
        + '.line-clamp-2{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;text-overflow:ellipsis;white-space:normal!important}' \
        + '.line-clamp-3{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;text-overflow:ellipsis;white-space:normal!important}' \
        + '.detail-panel{padding:24px;background:#f8fafc;border-left:4px solid #cbd5e1}' \
        + '.detail-panel.risk-high{border-left-color:#dc2626}' \
        + '.detail-panel.risk-medium{border-left-color:#d97706}' \
        + '.detail-panel.risk-low{border-left-color:#16a34a}' \
        + '.detail-header{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #e2e8f0}' \
        + '.detail-title{font-size:16px;font-weight:700;color:#111827;margin-bottom:6px;line-height:1.4}' \
        + '.detail-meta{font-size:13px;color:#64748b}' \
        + '.detail-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}' \
        + '.detail-section{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.05)}' \
        + '.detail-section h4{font-size:14px;font-weight:700;color:#111827;margin-bottom:12px;display:flex;align-items:center;gap:8px}' \
        + '.detail-section p{font-size:14px;color:#475569;line-height:1.7;margin:0}' \
        + '.detail-section ul{list-style:none;padding:0;margin:0}' \
        + '.detail-section li{position:relative;padding-left:18px;margin-bottom:10px;font-size:14px;color:#475569;line-height:1.6}' \
        + '.detail-section li::before{content:"";position:absolute;left:0;top:8px;width:6px;height:6px;border-radius:50%;background:#6b3a8a}' \
        + '.section-icon{font-size:16px}' \
        + '.category-tags{margin-top:14px}' \
        + '.category-tags h5{font-size:12px;font-weight:600;color:#64748b;margin:0 0 8px;text-transform:uppercase;letter-spacing:.5px}' \
        + '.detail-parties{margin-top:20px;background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.05)}' \
        + '.detail-parties h4{font-size:14px;font-weight:700;color:#111827;margin-bottom:14px}' \
        + '.detail-parties-table{width:100%;font-size:13px;border-collapse:collapse}' \
        + '.detail-parties-table th{background:#f1f5f9;padding:10px 14px;text-align:left;font-weight:600;color:#475569}' \
        + '.detail-parties-table td{padding:10px 14px;border-bottom:1px solid #e2e8f0;color:#475569}' \
        + '.detail-parties-table tr:last-child td{border-bottom:none}' \
        + '.detail-parties-table td:last-child{text-align:right;font-weight:600;color:#111827}' \
        + '.detail-source{display:inline-flex;align-items:center;gap:6px;margin-top:16px;padding:8px 14px;background:#fff;border:1px solid #d1d5db;border-radius:8px;color:#374151;font-size:13px;font-weight:500;text-decoration:none;transition:all .2s}' \
        + '.detail-source:hover{background:#f3f4f6;border-color:#9ca3af}' \
        + '.filter-btn{padding:6px 16px;border:1px solid #d1d5db;border-radius:9999px;font-size:14px;cursor:pointer;background:#fff;color:#374151;transition:all .2s}' \
        + '.filter-btn:hover{border-color:#5c2a7f;color:#6b3a8a}' \
        + '.filter-btn.active{background:#4a1f6b;border-color:#4a1f6b;color:#fff}' \
        + '.filter-btn.active:hover{background:#4a1f6b;border-color:#4a1f6b;color:#fff}' \
        + '@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}' \
        + '.fade-in{animation:fadeIn .6s ease-out}' \
        + 'th{font-size:12px;font-weight:600;color:#6b7280;padding:10px 12px;background:#f9fafb;text-align:left;cursor:pointer;user-select:none;vertical-align:middle}' \
        + 'th:hover{background:#f3f4f6}' \
        + 'th .sort-arrow{font-size:10px;color:#9ca3af;margin-left:3px}' \
        + 'th .sort-arrow.active{color:#6b3a8a}' \
        + 'td{font-size:13px;vertical-align:middle}' \
        + '.ai-assistant #aiToggle:hover{transform:scale(1.05)}' \
        + '.ai-assistant #aiDialog.active{display:flex !important}' \
        + '.ai-msg{display:flex;margin-bottom:12px}' \
        + '.ai-bot{justify-content:flex-start}' \
        + '.ai-user{justify-content:flex-end}' \
        + '.ai-msg>div{max-width:280px;padding:10px 14px;border-radius:12px;font-size:13px;line-height:1.6;word-break:break-word}' \
        + '.ai-bot>div{background:#fff;border:1px solid #e5e7eb;color:#374151}' \
        + '.ai-user>div{background:#5c2a7f;color:#fff}' \
        + '.ai-loading{color:#6b7280;font-size:12px;margin-top:4px}' \
        + '</style></head><body>' \
        + '<header style="background:#fff;border-bottom:1px solid #e5e7eb;position:sticky;top:0;z-index:50">' \
        + '<div style="max-width:1400px;margin:0 auto;padding:0 24px;height:64px;display:flex;align-items:center;justify-content:space-between">' \
        + '<div style="display:flex;align-items:center;gap:12px">' \
        + '<img src="./logo.svg" alt="logo" style="height:32px;width:auto">' \
        + '<h1 style="font-size:18px;font-weight:700;color:#111827;margin:0">汽车金融行业监管处罚合规看板</h1>' \
        + '<span style="font-size:12px;padding:4px 12px;background:#f3f4f6;color:#374151;border-radius:6px;font-weight:500">上海金融监管局 · ' + year + '年</span></div>' \
        + '<div style="font-size:14px;color:#6b7280">更新于 ' + now + '</div></div></header>' \
        + '<main style="max-width:1400px;margin:0 auto;padding:24px">' \
        + '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:24px" class="fade-in">'         + '<div class="card kpi" style="padding:20px;border-left:4px solid #5c2a7f">'         + '<p style="font-size:14px;color:#6b7280;margin:0 0 4px">处罚案例数</p>'         + '<p style="font-size:32px;font-weight:700;color:#111827;margin:0">' + str(total) + ' <span style="font-size:16px;font-weight:400;color:#9ca3af">条</span></p>'         + '<p style="font-size:13px;color:#9ca3af;margin-top:8px">上海局 ' + year + ' 年（合并双罚后）</p></div>'         + '<div class="card kpi" style="padding:20px;border-left:4px solid #5c2a7f">'         + '<p style="font-size:14px;color:#6b7280;margin:0 0 4px">罚款总金额</p>'         + '<p style="font-size:32px;font-weight:700;color:#111827;margin:0">' + "{:,.0f}".format(total_amount) + ' <span style="font-size:16px;font-weight:400;color:#9ca3af">万元</span></p>'         + '<p style="font-size:13px;color:#9ca3af;margin-top:8px">含机构及个人罚款</p></div>'         + '<div class="card kpi" style="padding:20px;border-left:4px solid #dc2626">'         + '<p style="font-size:14px;color:#6b7280;margin:0 0 4px">高风险案例</p>'         + '<p style="font-size:32px;font-weight:700;color:#dc2626;margin:0">' + str(risk_stats["高"]) + ' <span style="font-size:16px;font-weight:400;color:#9ca3af">条</span></p>'         + '<p style="font-size:13px;color:#9ca3af;margin-top:8px">对汽车金融有直接映射</p></div>'         + '<div class="card kpi" style="padding:20px;border-left:4px solid #7c5aa3">'         + '<p style="font-size:14px;color:#6b7280;margin:0 0 4px">双罚案例</p>'         + '<p style="font-size:32px;font-weight:700;color:#7c5aa3;margin:0">' + str(dual_penalty_count) + ' <span style="font-size:16px;font-weight:400;color:#9ca3af">条</span></p>'         + '<p style="font-size:13px;color:#9ca3af;margin-top:8px">机构+个人同时被处罚</p></div></div>'         + '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:24px" class="fade-in">'         + '<div class="card kpi" style="padding:20px;border-left:4px solid #5c2a7f">'         + '<p style="font-size:14px;color:#6b7280;margin:0 0 4px">机构罚单</p>'         + '<p style="font-size:28px;font-weight:700;color:#111827;margin:0">' + str(org_count) + ' <span style="font-size:14px;font-weight:400;color:#9ca3af">条</span></p></div>'         + '<div class="card kpi" style="padding:20px;border-left:4px solid #5c2a7f">'         + '<p style="font-size:14px;color:#6b7280;margin:0 0 4px">个人罚单</p>'         + '<p style="font-size:28px;font-weight:700;color:#111827;margin:0">' + str(person_count) + ' <span style="font-size:14px;font-weight:400;color:#9ca3af">条</span></p></div>'         + '<div class="card kpi" style="padding:20px;border-left:4px solid #5c2a7f">'         + '<p style="font-size:14px;color:#6b7280;margin:0 0 4px">原始记录</p>'         + '<p style="font-size:28px;font-weight:700;color:#111827;margin:0">' + str(raw_count) + ' <span style="font-size:14px;font-weight:400;color:#9ca3af">条</span></p></div></div>'         + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px" class="fade-in">'         + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#5c2a7f;margin-bottom:16px">业务领域罚款金额分布</h3>'         + '<div id="chartField" style="height:320px"></div></div>'         + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#111827;margin-bottom:16px">风险等级分布</h3>'         + '<div id="chartRisk" style="height:320px"></div></div></div>'         + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px" class="fade-in">'         + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#5c2a7f;margin-bottom:16px">机构类型罚款金额分布</h3>'         + '<div id="chartInst" style="height:320px"></div></div>'         + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#5c2a7f;margin-bottom:16px">处罚类型分布</h3>'         + '<div id="chartPenaltyType" style="height:320px"></div></div></div>' \
        + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px" class="fade-in">' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#111827;margin-bottom:16px">罚款金额区间分布</h3>' \
        + '<div id="chartAmountRange" style="height:320px"></div></div>' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#111827;margin-bottom:16px">被处罚对象职级/身份分布</h3>' \
        + '<div id="chartPosition" style="height:320px"></div></div></div>' \
        + '<div style="display:grid;grid-template-columns:2fr 1fr;gap:24px;margin-bottom:24px" class="fade-in">' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#111827;margin-bottom:16px">重点违规事由 TOP15</h3>' \
        + '<div id="chartKeyword" style="height:360px"></div></div>' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#7c5aa3;margin-bottom:16px">单罚 vs 双罚</h3>' \
        + '<div id="chartDual" style="height:320px"></div></div></div>' \
        + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:24px" class="fade-in">' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#5c2a7f;margin-bottom:16px">二级分类 TOP15</h3>' \
        + '<div id="chartSubField" style="height:360px"></div></div>' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#6b3a8a;margin-bottom:16px">月度处罚趋势</h3>' \
        + '<div id="chartMonthly" style="height:360px"></div></div></div>' \
        + '<div style="display:grid;grid-template-columns:1fr;gap:24px;margin-bottom:24px" class="fade-in">' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#7c5aa3;margin-bottom:16px">各领域月度处罚趋势</h3>' \
        + '<div id="chartHeatmap" style="height:400px"></div></div></div>' \
        + '<div style="display:grid;grid-template-columns:1fr 2fr;gap:24px;margin-bottom:24px" class="fade-in">' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#6b3a8a;margin-bottom:16px">机构 vs 个人罚款金额占比</h3>' \
        + '<div id="chartPersonOrg" style="height:320px"></div></div>' \
        + '<div class="card" style="padding:20px"><h3 style="font-size:16px;font-weight:700;color:#5c2a7f;margin-bottom:16px">高额罚单（≥100万）概览</h3>' \
        + '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:16px">' \
        + '<div style="text-align:center;padding:16px;background:#f9fafb;border-radius:8px"><p style="font-size:13px;color:#6b7280;margin:0">百万级罚单数</p><p style="font-size:28px;font-weight:700;color:#111827;margin:0">' + str(million_plus_count) + '<span style="font-size:14px;color:#9ca3af;margin-left:4px">张</span></p></div>' \
        + '<div style="text-align:center;padding:16px;background:#f9fafb;border-radius:8px"><p style="font-size:13px;color:#6b7280;margin:0">百万级罚单金额</p><p style="font-size:28px;font-weight:700;color:#111827;margin:0">' + "{:,.0f}".format(million_plus_amount) + '<span style="font-size:14px;color:#9ca3af;margin-left:4px">万</span></p></div>' \
        + '<div style="text-align:center;padding:16px;background:#f9fafb;border-radius:8px"><p style="font-size:13px;color:#6b7280;margin:0">占总金额比例</p><p style="font-size:28px;font-weight:700;color:#111827;margin:0">' + ("{:.1f}%".format(million_plus_amount / total_amount * 100) if total_amount > 0 else "0%") + '</p></div></div>' \
        + '<p style="font-size:13px;color:#6b7280;margin:0">注：按单张罚单罚款金额 ≥ 100万元统计，同一案件内多条记录分别计算。</p></div></div>' \
        + '<div class="card" style="padding:16px 20px;display:flex;align-items:center;gap:12px;margin-bottom:24px;flex-wrap:wrap" class="fade-in">' \
        + '<span style="font-size:14px;font-weight:500;color:#6b7280">风险等级：</span>' \
        + '<button onclick="filterTable(\'all\')" class="filter-btn active" data-filter="all">全部</button>' \
        + '<button onclick="filterTable(\'高\')" class="filter-btn" data-filter="高">高风险</button>' \
        + '<button onclick="filterTable(\'中\')" class="filter-btn" data-filter="中">中风险</button>' \
        + '<button onclick="filterTable(\'低\')" class="filter-btn" data-filter="低">低风险</button>' \
        + '<span style="font-size:14px;font-weight:500;color:#6b7280;margin-left:16px">业务领域：</span>' \
        + '<select id="fieldFilter" onchange="filterField(this.value)" style="padding:6px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;background:#fff">' \
        + '<option value="all">全部领域</option>' + field_options + '</select>' \
        + '<span style="font-size:14px;font-weight:500;color:#6b7280;margin-left:16px">二级分类：</span>' \
        + '<select id="subFieldFilter" onchange="filterSubField(this.value)" style="padding:6px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;background:#fff">' \
        + '<option value="all">全部二级分类</option>' + sub_field_options + '</select>' \
        + '<input id="searchInput" oninput="searchTable()" placeholder="搜索当事人或违规事由..." ' \
        + 'style="padding:6px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;margin-left:auto;width:260px"></div>' \
        + '<div class="card fade-in" style="overflow:hidden">' \
        + '<div style="padding:20px;border-bottom:1px solid #f3f4f6">' \
        + '<h3 style="font-size:18px;font-weight:700;color:#111827;margin:0">处罚案例明细 · 汽车金融合规映射</h3>' \
        + '<p style="font-size:14px;color:#6b7280;margin-top:4px">同一案件包内的处罚来自同一监管公告；点击行展开查看汽车金融映射风险、合规建议及原文链接</p></div>' \
        + '<div style="overflow-x:auto">' \
        + '<table>' \
        + '<thead><tr>' \
        + '<th style="width:110px" onclick="sortTable(\'no\')">处罚文号<span class="sort-arrow active" id="sort-no">↑</span></th>' \
        + '<th style="width:90px">发布时间</th>' \
        + '<th style="width:155px">当事人</th>' \
        + '<th style="width:115px">业务领域</th>' \
        + '<th style="width:135px">二级分类</th>' \
        + '<th>主要违规事由</th>' \
        + '<th style="width:140px">行政处罚内容</th>' \
        + '<th style="width:90px;text-align:right" onclick="sortTable(\'amount\')">罚款金额<span class="sort-arrow" id="sort-amount"></span></th>' \
        + '<th style="width:70px">风险等级</th>' \
        + '<th style="width:210px">风险标签</th></tr></thead>' \
        + tbodies_html \
        + '</table></div></div>' \
        + '<div style="text-align:center;padding:20px;font-size:13px;color:#9ca3af">' \
        + '<p>本页面仅整理公开监管信息，不构成法律建议。数据来源：国家金融监督管理总局上海监管局。</p>' \
        + '<p style="margin-top:4px">生成时间：' + now_full + ' · 自动更新：每周一</p></div></main>' \
        + '<div id="aiAssistant" class="ai-assistant" style="position:fixed;bottom:24px;right:24px;z-index:100;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Noto Sans SC,sans-serif">' \
        + '<button id="aiToggle" onclick="toggleAI()" style="width:56px;height:56px;border-radius:50%;background:#5c2a7f;color:#fff;border:none;box-shadow:0 4px 12px rgba(0,0,0,.2);cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:24px;transition:transform .2s"><img src="ai_logo.jpg" style="width:48px;height:48px;border-radius:50%;object-fit:cover;" alt="AI"></button>' \
        + '<div id="aiDialog" style="display:none;width:380px;height:520px;background:#fff;border-radius:16px;box-shadow:0 8px 30px rgba(0,0,0,.18);position:absolute;bottom:72px;right:0;overflow:hidden;flex-direction:column">' \
        + '<div style="padding:16px 20px;background:#4a1f6b;color:#fff;display:flex;align-items:center;justify-content:space-between">' \
        + '<div style="display:flex;align-items:center;gap:10px"><img src="ai_logo.jpg" style="width:28px;height:28px;border-radius:50%;object-fit:cover;" alt="AI"><span style="font-weight:600;font-size:15px">AI 合规助手</span></div>' \
        + '<button onclick="toggleAI()" style="background:none;border:none;color:#fff;font-size:20px;cursor:pointer">&times;</button></div>' \
        + '<div id="aiMessages" style="flex:1;overflow-y:auto;padding:16px;background:#f9fafb">' \
        + '<div class="ai-msg ai-bot"><div style="font-size:13px;color:#374151;line-height:1.6">你好！我是基于当前处罚数据训练的合规助手。可以问我：<br>1. 今年高风险案例有哪些？<br>2. 信贷业务主要违规点是什么？<br>3. 有哪些合规管控建议？</div></div>' \
        + '</div>' \
        + '<div style="padding:12px 16px;border-top:1px solid #e5e7eb;background:#fff;display:flex;gap:8px">' \
        + '<input id="aiInput" type="text" placeholder="输入问题..." onkeydown="if(event.key===\'Enter\')sendAIQuestion()" style="flex:1;padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:14px;outline:none">' \
        + '<button onclick="sendAIQuestion()" style="padding:8px 16px;background:#6b3a8a;color:#fff;border:none;border-radius:8px;font-size:14px;cursor:pointer;font-weight:500">发送</button></div></div></div>' \
        + '<script>' \
        + 'const fieldData=' + field_chart + ';' \
        + 'const riskData=' + risk_chart + ';' \
        + 'const instData=' + inst_dual_chart + ';' \
        + 'const penaltyTypeData=' + penalty_type_dual_chart + ';' \
        + 'const amountRangeData=' + amount_range_chart + ';' \
        + 'const positionData=' + position_chart + ';' \
        + 'const keywordData=' + keyword_chart + ';' \
        + 'const dualData=' + dual_chart + ';' \
        + 'const personOrgData=' + person_org_amount_chart + ';' \
        + 'const subFieldData=' + sub_field_chart + ';' \
        + 'const monthlyData=' + monthly_chart + ';' \
        + 'const heatmapData=' + heatmap_data_json + ';' \
        + 'const heatmapFields=' + heatmap_fields_json + ';' \
        + 'const heatmapMonths=' + heatmap_months_json + ';' \
        + 'const systemPrompt=' + json.dumps(system_prompt, ensure_ascii=False) + ';' \
        + 'const caseSummary=' + case_summary_json + ';' \
        + 'const llmConfig={apiKey:' + json.dumps(LLM_API_KEY, ensure_ascii=False) + ',apiBase:' + json.dumps(LLM_API_BASE, ensure_ascii=False) + ',model:' + json.dumps(LLM_MODEL, ensure_ascii=False) + '};' \
        + 'const chartInstances=[];' \
        + 'function makeHorizontalBar(id,title,data,color){const c=echarts.init(document.getElementById(id));c.setOption({tooltip:{trigger:"axis",axisPointer:{type:"shadow"},formatter:function(p){return p[0].name+"<br/>"+p[0].marker+title+"："+p[0].value+(title.includes("数量")?"条":"万");}},grid:{left:"3%",right:"8%",bottom:"3%",top:"3%",containLabel:true},xAxis:{type:"value",axisLabel:{formatter:title.includes("数量")?"{value}条":"{value}万"},splitLine:{lineStyle:{type:"dashed"}}},yAxis:{type:"category",data:data.map(d=>d.name).reverse(),axisLabel:{fontSize:12}},series:[{type:"bar",data:data.map(d=>title.includes("数量")?(d.count!==undefined?d.count:d.value):(d.amount!==undefined?d.amount:d.value)).reverse(),itemStyle:{color:color,borderRadius:[0,4,4,0]},label:{show:true,position:"right",formatter:function(p){return p.value+(title.includes("数量")?"条":"万");},fontSize:11}}]});chartInstances.push(c);return c;}' \
        + 'function makePie(id,data,colors){const c=echarts.init(document.getElementById(id));c.setOption({tooltip:{trigger:"item",formatter:"{b}:{c}({d}%)"},legend:{orient:"vertical",right:10,top:"center",show:data.length<=6},series:[{type:"pie",radius:["40%","70%"],center:["40%","50%"],data:data,itemStyle:{borderRadius:6,borderColor:"#fff",borderWidth:2},label:{show:true,formatter:"{b}\\n{c}"}}]});if(colors)c.setOption({color:colors});chartInstances.push(c);return c;}' \
        + 'makeHorizontalBar("chartField","罚款金额（万）",fieldData,"#5c2a7f");' \
        + 'makePie("chartRisk",riskData.map(d=>({value:d.value,name:d.name+"风险"})),["#dc2626","#d97706","#16a34a"]);' \
        + 'makeHorizontalBar("chartInst","罚款金额（万）",instData,"#6b3a8a");' \
        + 'makeHorizontalBar("chartPenaltyType","罚单数量（条）",penaltyTypeData,"#6b3a8a");' \
        + 'makeHorizontalBar("chartAmountRange","罚单数量（条）",amountRangeData,"#0891b2");' \
        + 'makeHorizontalBar("chartPosition","罚款金额（万）",positionData,"#059669");' \
        + 'const ck=echarts.init(document.getElementById("chartKeyword"));ck.setOption({tooltip:{trigger:"item",formatter:"{b}:{c}条"},grid:{left:"3%",right:"4%",bottom:"3%",top:"3%",containLabel:true},xAxis:{type:"category",data:keywordData.map(d=>d.name),axisLabel:{rotate:30,fontSize:11}},yAxis:{type:"value",axisLabel:{formatter:"{value}条"}},series:[{type:"bar",data:keywordData.map(d=>d.value),itemStyle:{color:"#b45309",borderRadius:[4,4,0,0]},label:{show:true,position:"top",formatter:"{c}"}}]});chartInstances.push(ck);' \
        + 'makePie("chartDual",dualData,["#6b7280","#7c5aa3"]);' \
        + 'makePie("chartPersonOrg",personOrgData,["#6b3a8a","#ea580c"]);' \
        + 'makeHorizontalBar("chartSubField","罚款金额（万）",subFieldData,"#7c5aa3");' \
        + 'const monthlyMax=Math.max(...monthlyData.map(d=>d.amount));const monthlyYMax=Math.ceil(monthlyMax/100)*100;' \
        + 'const cm=echarts.init(document.getElementById("chartMonthly"));cm.setOption({tooltip:{trigger:"axis",formatter:function(p){let s=p[0].name; p.forEach(i=>{s+="<br/>"+i.marker+i.seriesName+"："+i.value+(i.seriesName==="案例数"?"条":"万");});return s;}},grid:{left:"3%",right:"4%",bottom:"3%",top:"12%",containLabel:true},legend:{data:["案例数","罚款金额"],top:0},xAxis:{type:"category",data:monthlyData.map(d=>d.month),boundaryGap:true,axisLabel:{rotate:30,fontSize:11}},yAxis:[{type:"value",name:"案例数",min:0,axisLabel:{formatter:"{value}条"},splitLine:{show:true,lineStyle:{type:"dashed"}}},{type:"value",name:"罚款金额(万)",min:0,max:monthlyYMax,alignTicks:true,axisLabel:{formatter:"{value}万"},splitLine:{show:false}}],series:[{name:"案例数",type:"bar",data:monthlyData.map(d=>d.count),itemStyle:{color:"#7c5aa3",borderRadius:[4,4,0,0]}},{name:"罚款金额",type:"line",yAxisIndex:1,data:monthlyData.map(d=>d.amount),itemStyle:{color:"#dc2626"},smooth:true}]});chartInstances.push(cm);' \
        + 'const hm=echarts.init(document.getElementById("chartHeatmap"));hm.setOption({tooltip:{position:"top",formatter:function(p){return p.name+"<br/>"+heatmapMonths[p.value[0]]+" | "+heatmapFields[p.value[1]]+"<br/>案件数："+p.value[2];}},grid:{left:"12%",right:"8%",bottom:"12%",top:"5%"},xAxis:{type:"category",data:heatmapMonths,splitArea:{show:true},axisLabel:{rotate:30,fontSize:11}},yAxis:{type:"category",data:heatmapFields,splitArea:{show:true},axisLabel:{fontSize:12}},visualMap:{min:0,max:10,calculable:true,orient:"horizontal",left:"center",bottom:"0%",inRange:{color:["#f0f9ff","#bae6fd","#7dd3fc","#38bdf8","#0ea5e9","#0284c7"]}},series:[{name:"案件数",type:"heatmap",data:heatmapData,label:{show:true,fontSize:12},emphasis:{itemStyle:{shadowBlur:10,shadowColor:"rgba(0,0,0,0.5)"}}}]});chartInstances.push(hm);' \
        + 'function toggleDetail(row){const d=row.nextElementSibling;if(d&&d.classList.contains("detail-row")){const willShow=d.classList.contains("hidden");d.classList.toggle("hidden");row.classList.toggle("expanded",willShow);}}' \
        + 'let sortField="no",sortDir=1;' \
        + 'function sortTable(field){' \
        + 'if(sortField===field)sortDir*=-1;else{sortField=field;sortDir=1;}' \
        + 'document.getElementById("sort-no").textContent=sortField==="no"?(sortDir===1?"↑":"↓"):"";' \
        + 'document.getElementById("sort-no").className="sort-arrow"+(sortField==="no"?" active":"");' \
        + 'document.getElementById("sort-amount").textContent=sortField==="amount"?(sortDir===1?"↑":"↓"):"";' \
        + 'document.getElementById("sort-amount").className="sort-arrow"+(sortField==="amount"?" active":"");' \
        + 'const table=document.querySelector("table");' \
        + 'const groups=Array.from(document.querySelectorAll(".case-group"));' \
        + 'groups.sort((a,b)=>{' \
        + 'const va=parseFloat(a.getAttribute("data-sort-"+field))||0;' \
        + 'const vb=parseFloat(b.getAttribute("data-sort-"+field))||0;' \
        + 'return(va-vb)*sortDir});' \
        + 'groups.forEach(g=>table.appendChild(g));}' \
        + 'let rF="all",fF="all",sfF="all",sF="";' \
        + 'function apply(){document.querySelectorAll(".case-group").forEach(g=>{let show=false;g.querySelectorAll(".case-row").forEach(row=>{const r=row.querySelector("td:nth-child(9)").innerText.trim();' \
        + 'const f=row.querySelector("td:nth-child(4)").innerText.trim();' \
        + 'const sf=row.querySelector("td:nth-child(5)").innerText.trim();' \
        + 'const p=row.querySelector("td:nth-child(3)").innerText.toLowerCase();' \
        + 'const v=row.querySelector("td:nth-child(6)").innerText.toLowerCase();' \
        + 'const rm=rF==="all"||r.includes(rF);const fm=fF==="all"||f.includes(fF);' \
        + 'const sfm=sfF==="all"||sf.includes(sfF);' \
        + 'const sm=!sF||p.includes(sF)||v.includes(sF);' \
        + 'if(rm&&fm&&sfm&&sm){row.style.display="";const d=row.nextElementSibling;if(d)d.style.display="";show=true;}else{row.style.display="none";const d=row.nextElementSibling;if(d)d.style.display="none";}});' \
        + 'g.style.display=show?"":"none";});}' \
        + 'function filterTable(risk){rF=risk;document.querySelectorAll(".filter-btn").forEach(b=>b.classList.remove("active"));' \
        + 'event.target.classList.add("active");apply();}' \
        + 'function filterField(field){fF=field;apply();}' \
        + 'function filterSubField(subField){sfF=subField;apply();}' \
        + 'function searchTable(){sF=document.getElementById("searchInput").value.toLowerCase();apply();}' \
        + 'function toggleAI(){const d=document.getElementById("aiDialog");if(d.style.display==="none"||!d.style.display){d.style.display="flex";d.classList.add("active");}else{d.style.display="none";d.classList.remove("active");}}' \
        + 'function appendMessage(role,text){const container=document.getElementById("aiMessages");const wrapper=document.createElement("div");wrapper.className="ai-msg ai-"+role;const bubble=document.createElement("div");bubble.innerHTML=text.replace(/\\n/g,"<br>");wrapper.appendChild(bubble);container.appendChild(wrapper);container.scrollTop=container.scrollHeight;return bubble;}' \
        + 'function appendLoading(){const container=document.getElementById("aiMessages");const wrapper=document.createElement("div");wrapper.className="ai-msg ai-bot";const bubble=document.createElement("div");bubble.innerHTML="<span class=\\"ai-loading\\">思考中...</span>";wrapper.appendChild(bubble);container.appendChild(wrapper);container.scrollTop=container.scrollHeight;return bubble;}' \
        + 'async function sendAIQuestion(){const input=document.getElementById("aiInput");const q=input.value.trim();if(!q)return;appendMessage("user",q);input.value="";const bubble=appendLoading();try{const messages=[{role:"system",content:systemPrompt+"\\n\\n案例数据："+JSON.stringify(caseSummary)},{role:"user",content:q}];const resp=await fetch("https://auto-finance-ai.autofinance.workers.dev/api/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({model:llmConfig.model,messages:messages,temperature:1,max_tokens:4096}),cache:"no-store",keepalive:false});if(!resp.ok){const data=await resp.json();let errMsg="HTTP "+resp.status;if(data.error&&data.error.message){errMsg+="："+data.error.message;}else if(data.message){errMsg+="："+data.message;}bubble.parentElement.remove();appendMessage("bot","请求失败："+errMsg);return;}bubble.innerHTML="";bubble.parentElement.classList.remove("ai-loading");const reader=resp.body.getReader();const decoder=new TextDecoder();let buffer="";let fullText="";while(true){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});const lines=buffer.split("\\n");buffer=lines.pop();for(const line of lines){const t=line.trim();if(!t||!t.startsWith("data: "))continue;const ds=t.slice(6);if(ds==="[DONE]")continue;try{const chunk=JSON.parse(ds);if(chunk.choices&&chunk.choices[0]&&chunk.choices[0].delta&&chunk.choices[0].delta.content){fullText+=chunk.choices[0].delta.content;bubble.innerHTML=fullText.replace(/\\n/g,"<br>");document.getElementById("aiMessages").scrollTop=document.getElementById("aiMessages").scrollHeight;}}catch(e){}}}if(!fullText){bubble.innerHTML="抱歉，没有获得有效回答。";}}catch(e){bubble.parentElement.remove();appendMessage("bot","请求异常："+e.message);}}' \
        + 'window.addEventListener("resize",()=>{chartInstances.forEach(c=>c.resize());});' \
        + '</script></body></html>'

    return html


def main():
    os.makedirs("dist", exist_ok=True)

    if not os.path.exists(DATA_FILE):
        print("[ERROR] 找不到增强数据文件: " + DATA_FILE)
        print("请先运行: python scripts/enrich.py")
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        enriched_data = json.load(f)

    # 数据格式校验：确保关键字段存在
    required_fields = {"doc_id", "doc_no", "party", "field", "amount",
                       "level", "tags", "mapping", "advice", "penalty"}
    processed = []
    for r in enriched_data:
        missing = required_fields - set(r.keys())
        if missing:
            print(f"[WARN] 记录缺少字段 {missing}: doc_id={r.get('doc_id')}, party={r.get('party')}")
            continue
        processed.append(r)

    groups = group_by_doc_id(processed)

    year = "2026"
    html = build_html(groups, year, len(processed))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print("[DONE] 网页已生成: " + OUTPUT_FILE)
    print("  - 案件包数量: " + str(len(groups)) + " 组")
    print("  - 合并后处罚案例: " + str(sum(len(g["sub_items"]) for g in groups)) + " 条")
    print("  - 原始记录: " + str(len(processed)) + " 条")


if __name__ == "__main__":
    main()