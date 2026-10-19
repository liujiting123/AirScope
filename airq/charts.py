"""不用联网字体或图表库的 SVG 绘图和独立 HTML 报告。"""
from __future__ import annotations
import csv
import html
import io
import json
import math
import re
from pathlib import Path

COLORS = ["#087f73", "#d88842", "#5679ae", "#ae6372", "#778a51", "#7968a2"]


def esc(value):
    return html.escape(str(value), quote=True)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def chart_svg(chart: dict, width: int = 960, height: int = 420) -> str:
    labels, series = chart.get("labels", []), chart.get("series", [])
    title, unit = chart.get("title", "统计图"), chart.get("unit", "")
    left, top, right, bottom = 76, 76, width - 30, height - 76
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">',
             '<rect width="100%" height="100%" rx="12" fill="#fffdf8"/>',
             '<g font-family="PingFang SC, Microsoft YaHei, sans-serif" fill="#203d38">',
             f'<text x="28" y="34" font-size="20" font-weight="600">{esc(title)}</text>',
             f'<text x="28" y="56" font-size="12" fill="#63766f">{esc(unit)}</text>']
    vals = [v for s in series for v in s.get("values", []) if number(v)]
    threshold = chart.get("threshold")
    if number(threshold):
        vals.append(threshold)
    maximum = max(max(vals or [1]), 0) * 1.15
    minimum = min(min(vals or [0]), 0) * 1.15
    if maximum == minimum:
        maximum = minimum + 1
    if chart.get("type") == "donut":
        values = series[0].get("values", []) if series else []
        total = sum(v for v in values if number(v) and v > 0)
        radius, cx, cy = 105, width * .32, height * .55
        cumulative = 0
        if not total:
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="#e5ece6" stroke-width="42"/>')
        for i, v in enumerate(values):
            if not number(v) or v <= 0 or not total:
                continue
            fraction = v / total
            circumference = 2 * math.pi * radius
            parts.append(f'<circle cx="{cx}" cy="{cy}" r="{radius}" fill="none" stroke="{COLORS[i % len(COLORS)]}" stroke-width="42" stroke-dasharray="{fraction*circumference} {circumference}" stroke-dashoffset="{-cumulative*circumference}" transform="rotate(-90 {cx} {cy})"><title>{esc(labels[i] if i < len(labels) else i)}：{v}，{fraction:.1%}</title></circle>')
            cumulative += fraction
        parts.append(f'<text x="{cx}" y="{cy}" text-anchor="middle" font-size="34">{total:g}</text>')
        for i, label in enumerate(labels):
            value = values[i] if i < len(values) else None
            parts.append(f'<rect x="570" y="{110+i*32}" width="12" height="12" fill="{COLORS[i%len(COLORS)]}"/><text x="594" y="{121+i*32}" font-size="15">{esc(label)} · {esc(value)}</text>')
    else:
        y = lambda v: bottom - (v - minimum) / (maximum - minimum) * (bottom - top)
        for tick in range(5):
            v = minimum + (maximum - minimum) * tick / 4
            yy = y(v)
            parts.extend([f'<path d="M{left} {yy}H{right}" stroke="#e4eae4" stroke-width="1"/>',
                          f'<text x="{left-12}" y="{yy+4}" text-anchor="end" font-size="12">{v:.0f}</text>'])
        if number(threshold):
            parts.extend([f'<path d="M{left} {y(threshold)}H{right}" stroke="#ad6b4c" stroke-dasharray="6 5"/>',
                          f'<text x="{right}" y="{y(threshold)-6}" text-anchor="end" font-size="11" fill="#ad6b4c">{esc(chart.get("threshold_label", "参考限值"))} {threshold:g}</text>'])
        n = max(len(labels), 1)
        for i, label in enumerate(labels):
            xx = left + (i + .5) / n * (right - left)
            if n <= 16 or i % max(1, n // 12) == 0:
                parts.append(f'<text x="{xx}" y="{bottom+24}" text-anchor="middle" font-size="12">{esc(label)}</text>')
        for si, item in enumerate(series):
            color, points = COLORS[si % len(COLORS)], []
            for i, value in enumerate(item.get("values", [])):
                xx = left + (i + .5) / n * (right - left)
                if not number(value):
                    if points:
                        parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2.5"/>')
                    points = []
                    continue
                yy = y(value)
                tooltip = esc(f'{item.get("name", "")} · {labels[i] if i < len(labels) else i}：{value:g} {unit}')
                if chart.get("type") == "bar":
                    bw = min(45, (right-left)/n*.65/max(len(series), 1))
                    bx = xx + (si-(len(series)-1)/2)*bw - bw/2
                    parts.append(f'<rect x="{bx}" y="{min(yy,y(0))}" width="{bw*.9}" height="{abs(y(0)-yy)}" rx="3" fill="{color}"><title>{tooltip}</title></rect>')
                    if n <= 12:
                        parts.append(f'<text x="{bx+bw*.45}" y="{yy-8 if value >= 0 else yy+16}" text-anchor="middle" font-size="12">{value:g}</text>')
                else:
                    points.append(f'{xx},{yy}')
                    parts.append(f'<circle cx="{xx}" cy="{yy}" r="3.5" fill="{color}"><title>{tooltip}</title></circle>')
            if points:
                parts.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2.5"/>')
            lx = 50 + (si % 6) * 145
            ly = height - 18 - (si // 6) * 18
            parts.append(f'<rect x="{lx}" y="{ly-10}" width="10" height="10" fill="{color}"/><text x="{lx+17}" y="{ly}" font-size="12">{esc(item.get("name", ""))}</text>')
    parts.append('</g></svg>')
    return ''.join(parts)


def safe_csv(value):
    """避免导出到 Excel 时把城市/文本字段误解释为公式。"""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def answer_html(text: str) -> str:
    """展示模型的公开答复，先转义再渲染少量 Markdown。"""
    safe = esc(text)
    safe = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", safe)
    safe = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", safe)
    return ''.join('<p>' + part.replace('\n', '<br>') + '</p>' for part in safe.split('\n\n') if part.strip())


def analysis_html(analysis: dict) -> str:
    """独立报告保留每次分析的图表和表格，支持一个问题调用多次分析。"""
    table = analysis.get("table", {})
    columns = table.get("columns", [])
    rows = table.get("rows", [])
    heading = ''.join(f'<th>{esc(c["label"])}</th>' for c in columns)
    body = ''.join('<tr>' + ''.join(f'<td>{esc(row.get(c["key"], "—"))}</td>' for c in columns) + '</tr>' for row in rows)
    figures = ''.join(chart_svg(chart) for chart in analysis.get("charts", []))
    standard = analysis.get("standard", {})
    content = f'<section class="analysis-section"><h2>{esc(analysis.get("title", "分析结果"))}</h2><p>{esc(analysis.get("summary", ""))}</p>{figures}'
    if columns:
        content += f'<div class="scroll"><table><thead><tr>{heading}</tr></thead><tbody>{body}</tbody></table></div>'
    if standard:
        content += f'<p class="meta">{esc(standard.get("name", ""))} · {esc(standard.get("basis", ""))} · {esc(standard.get("note", ""))}</p>'
    return content + '</section>'


def export_result(root: Path, result: dict) -> dict:
    folder = root / "outputs" / result["id"]
    folder.mkdir(parents=True, exist_ok=True)
    columns = result.get("table", {}).get("columns", [])
    rows = result.get("table", {}).get("rows", [])
    with (folder / "table.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([c["label"] for c in columns])
        for row in rows:
            writer.writerow([safe_csv(row.get(c["key"], "")) for c in columns])
    svgs = []
    for i, chart in enumerate(result.get("charts", []), 1):
        svg = chart_svg(chart)
        (folder / f"chart-{i}.svg").write_text(svg, encoding="utf-8")
        svgs.append(svg)
    heading = ''.join(f'<th>{esc(c["label"])}</th>' for c in columns)
    body = ''.join('<tr>' + ''.join(f'<td>{esc(row.get(c["key"], "—"))}</td>' for c in columns) + '</tr>' for row in rows)
    warnings = ''.join(f'<li>{esc(x)}</li>' for x in result.get("warnings", []))
    trace = ''.join(f'<li><strong>{esc(t.get("label",t.get("tool","工具")))}</strong> · {esc(t.get("observation",""))} · {t.get("duration_ms",0):.1f} ms'
                    + (f'<details><summary>调用参数</summary><pre>{esc(json.dumps(t["arguments"],ensure_ascii=False,indent=2))}</pre></details>' if t.get("arguments") else '') + '</li>'
                    for t in result.get("trace", []))
    standard = result.get("standard", {})
    source = result.get("provenance", {})
    model = result.get("model", "")
    ai_answer = '<section class="answer"><h2>DeepSeek 分析答复</h2>' + answer_html(result["answer"]) + '</section>' if result.get("answer") else ''
    analyses = result.get("analyses") or ([result] if columns or svgs else [])
    analysis_sections = ''.join(analysis_html(item) for item in analyses)
    engine_note = f'DeepSeek 模型 {model} 负责理解、规划与回答，本地工具完成统计计算。' if result.get("provider") == "deepseek" else '本报告由本地真实数据计算。'
    report = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(result['title'])} · 清穹</title>
<style>body{{margin:0;background:#f4f4ed;color:#203d38;font:16px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{max-width:1050px;margin:auto;padding:48px 24px}}header{{border-bottom:2px solid #087f73;padding-bottom:25px}}h1{{font-size:34px;line-height:1.3}}h2{{font-size:22px;margin-top:40px}}.meta{{font-size:13px;color:#4d6962}}svg{{width:100%;height:auto;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:14px}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #ccd8d0}}.scroll{{overflow:auto}}.notice{{background:#e9e9dd;padding:20px}}.answer{{background:#fffdf8;padding:1px 28px 20px;margin-top:25px;border-radius:12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}}li{{margin-bottom:12px}}summary{{cursor:pointer}}@media print{{body{{background:white}}main{{padding:0}}svg,table{{break-inside:avoid}}}}</style>
<main><header><p>清穹 AirScope · 本地分析报告</p><h1>{esc(result['title'])}</h1><p>{esc(result.get('summary',''))}</p><p class="meta">{esc(result.get('created_at',''))} · 查询：{esc(result['query'])}</p></header>
{ai_answer}{analysis_sections}
<details><summary>数据完整性说明</summary><ul>{warnings}</ul></details>
<h2>工具执行记录</h2><ol>{trace}</ol><h2>来源与复现</h2><p class="meta">{esc(source.get('name',''))} · 数据 SHA256：{esc(source.get('sha256',''))}</p><p><a href="{esc(source.get('source_url','#'))}">数据来源</a> · {esc(engine_note)}</p><p class="notice">{esc(result.get('insight',''))}</p></main></html>'''
    (folder / "report.html").write_text(report, encoding="utf-8")
    base = f"/outputs/{result['id']}"
    downloads = {"json": f"{base}/result.json", "csv": f"{base}/table.csv", "html": f"{base}/report.html"}
    if svgs:
        downloads["svg"] = f"{base}/chart-1.svg"
    result["downloads"] = downloads
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return downloads
