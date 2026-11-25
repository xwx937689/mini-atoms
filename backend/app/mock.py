"""Mock generator - only used as graceful degradation when no API key is configured."""
import json
from datetime import datetime

QUALITY_DASHBOARD = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>接口自动化测试质量看板</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:#f4f6fa;color:#1f2937;font-size:14px}
.side{position:fixed;left:0;top:0;bottom:0;width:210px;background:#111827;color:#d1d5db;padding:22px 14px}
.side .logo{font-size:17px;font-weight:700;color:#fff;margin-bottom:26px;padding:0 10px}
.side a{display:block;padding:11px 12px;border-radius:8px;color:#9ca3af;text-decoration:none;margin-bottom:4px;cursor:pointer}
.side a.on,.side a:hover{background:#1f2937;color:#fff}
.main{margin-left:210px;padding:26px 30px}
.top{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}
.top h1{font-size:22px;font-weight:700}.sub{color:#6b7280;font-size:13px;margin-top:4px}
.badge{background:#ecfdf5;color:#059669;padding:6px 12px;border-radius:20px;font-size:12px;font-weight:600}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:20px}
.card{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.card .l{color:#6b7280;font-size:13px}.card .v{font-size:28px;font-weight:700;margin-top:8px}
.card .d{font-size:12px;margin-top:6px;color:#9ca3af}.up{color:#059669}.down{color:#dc2626}
.panel{background:#fff;border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:20px}
.panel h2{font-size:15px;font-weight:600;margin-bottom:16px}
.filters{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
select,input{padding:8px 12px;border:1px solid #d1d5db;border-radius:8px;font-size:13px;background:#fff;outline:none}
table{width:100%;border-collapse:collapse;font-size:13px}th{text-align:left;color:#6b7280;font-weight:500;padding:10px 8px;border-bottom:1px solid #e5e7eb}
td{padding:11px 8px;border-bottom:1px solid #f3f4f6}tr:hover td{background:#f9fafb}
.st{padding:3px 9px;border-radius:12px;font-size:12px;font-weight:500}.pass{background:#ecfdf5;color:#059669}.fail{background:#fef2f2;color:#dc2626}
.bar{height:8px;background:#f3f4f6;border-radius:4px;overflow:hidden}.bar i{display:block;height:100%;background:#6366f1;border-radius:4px}
.chart{display:flex;align-items:flex-end;gap:14px;height:160px;padding-top:10px}
.chart .col{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px}
.chart .bars{width:100%;height:130px;display:flex;align-items:flex-end;justify-content:center;gap:3px}
.chart .bars i{width:16px;background:#6366f1;border-radius:3px 3px 0 0}
.chart .bars i.f{background:#f87171}
.chart span{font-size:11px;color:#9ca3af}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:50}
.modal.show{display:flex}.box{background:#fff;border-radius:14px;width:min(640px,90vw);padding:24px;max-height:80vh;overflow:auto}
.box h3{font-size:17px;margin-bottom:14px}.box pre{background:#111827;color:#e5e7eb;padding:14px;border-radius:8px;font-size:12px;overflow:auto;margin:8px 0}
.close{float:right;cursor:pointer;color:#9ca3af;font-size:20px;border:0;background:none}
.btn{padding:8px 14px;background:#6366f1;color:#fff;border:0;border-radius:8px;cursor:pointer;font-size:13px}
</style></head><body>
<div class="side"><div class="logo">◈ QA Atlas</div>
<a class="on" onclick="show('overview')">概览看板</a><a onclick="show('cases')">用例管理</a><a onclick="show('services')">服务监控</a><a onclick="show('reports')">测试报告</a></div>
<div class="main"><div class="top"><div><h1>接口自动化测试质量看板</h1><div class="sub">最后执行：2026-11-25 17:30 · 耗时 4m 32s</div></div><div class="badge">● 执行完成</div></div>
<div class="cards">
<div class="card"><div class="l">接口总数</div><div class="v">248</div><div class="d">覆盖 12 个服务</div></div>
<div class="card"><div class="l">测试通过率</div><div class="v">94.4%</div><div class="d up">↑ 2.1% 较上周</div></div>
<div class="card"><div class="l">失败用例</div><div class="v" style="color:#dc2626">14</div><div class="d down">需关注 3 个服务</div></div>
<div class="card"><div class="l">平均响应时间</div><div class="v">186ms</div><div class="d">P95 512ms</div></div>
</div>
<div class="panel"><h2>近 7 天执行趋势</h2><div class="chart" id="chart"></div></div>
<div class="panel"><h2>失败用例明细</h2>
<div class="filters"><select id="svc" onchange="render()"><option value="">全部服务</option><option>user-service</option><option>order-service</option><option>payment-service</option><option>inventory-service</option></select>
<input id="kw" placeholder="搜索用例名称..." oninput="render()" style="min-width:220px">
<button class="btn" onclick="render()">筛选</button></div>
<table><thead><tr><th>用例名称</th><th>所属服务</th><th>状态</th><th>耗时</th><th>执行时间</th><th>操作</th></tr></thead><tbody id="tbody"></tbody></table></div>
</div></div>
<div class="modal" id="modal"><div class="box"><button class="close" onclick="closeModal()">×</button><h3 id="mtitle">失败详情</h3>
<div id="mbody"></div></div></div>
<script>
const cases=[
{n:'创建订单-库存扣减',s:'order-service',t:1820,time:'17:28:41'},
{n:'支付回调幂等校验',s:'payment-service',t:956,time:'17:28:12'},
{n:'用户登录-Token刷新',s:'user-service',t:634,time:'17:27:55'},
{n:'库存预占并发',s:'inventory-service',t:2210,time:'17:27:30'},
{n:'订单取消-退款联动',s:'order-service',t:1120,time:'17:26:58'},
{n:'优惠券叠加计算',s:'payment-service',t:780,time:'17:26:20'},
{n:'批量下单-限流',s:'order-service',t:3450,time:'17:25:44'},
{n:'地址校验-港澳台',s:'user-service',t:512,time:'17:25:10'},
{n:'库存同步-超卖防护',s:'inventory-service',t:1890,time:'17:24:36'},
{n:'退款金额精度',s:'payment-service',t:672,time:'17:24:02'}];
const trend=[[210,8],[255,6],[198,12],[268,5],[240,9],[290,14],[248,14]];
const days=['11-19','11-20','11-21','11-22','11-23','11-24','11-25'];
function drawChart(){const box=document.getElementById('chart');box.innerHTML='';trend.forEach((v,i)=>{const c=document.createElement('div');c.className='col';
const bars=document.createElement('div');bars.className='bars';
const a=document.createElement('i');a.style.height=(v[0]/320*130)+'px';
const b=document.createElement('i');b.className='f';b.style.height=(v[1]/16*130)+'px';
bars.appendChild(a);bars.appendChild(b);c.appendChild(bars);
const s=document.createElement('span');s.textContent=days[i];c.appendChild(s);box.appendChild(c)})}
function render(){const svc=document.getElementById('svc').value;const kw=document.getElementById('kw').value.trim();
const rows=cases.filter(c=>(!svc||c.s===svc)&&(!kw||c.n.includes(kw)));
document.getElementById('tbody').innerHTML=rows.map((c,i)=>`<tr><td>${c.n}</td><td>${c.s}</td><td><span class="st fail">失败</span></td><td>${c.t}ms</td><td>${c.time}</td><td><button class="btn" onclick="openModal(${i})">详情</button></td></tr>`).join('')||'<tr><td colspan="6" style="text-align:center;color:#9ca3af">无匹配用例</td></tr>';
window._rows=rows}
function openModal(i){const c=window._rows[i];document.getElementById('mtitle').textContent='失败详情 · '+c.n;
document.getElementById('mbody').innerHTML=`<p><b>服务：</b>${c.s}</p><p><b>耗时：</b>${c.t}ms</p><p style="margin-top:12px"><b>请求参数</b></p><pre>${JSON.stringify({endpoint:'/api/v1/'+c.s.split('-')[0]+'/create',method:'POST',body:{orderId:'ORD20261125'+i,amount:199.00}},null,2)}</pre><p><b>响应内容</b></p><pre>${JSON.stringify({code:500,message:'Internal Server Error',trace:'java.lang.NullPointerException at OrderService.deduct(OrderService.java:142)'},null,2)}</pre>`;
document.getElementById('modal').classList.add('show')}
function closeModal(){document.getElementById('modal').classList.remove('show')}
function show(n){}drawChart();render()</script></body></html>"""


def plan(prompt: str) -> dict:
    return {
        "app_name": "接口自动化测试质量看板",
        "summary": "展示接口测试核心指标、执行趋势与失败用例明细的交互式看板",
        "pages": ["概览看板", "失败用例明细"],
        "features": ["服务筛选", "用例搜索", "失败详情弹窗", "7日执行趋势图", "核心指标卡片"],
        "layout": "左侧固定导航 + 右侧主内容区，顶部指标卡 + 中部趋势图 + 底部明细表",
        "components": ["指标卡片", "趋势柱状图", "筛选器", "数据表格", "详情弹窗"],
        "data": ["接口用例", "服务列表", "执行趋势"],
        "style": "现代化后台管理界面，浅色主题，靛蓝主色，卡片式布局",
        "acceptance": ["指标卡展示接口总数/通过率/失败数", "趋势图展示近7天数据", "支持服务筛选与搜索", "点击失败用例弹出详情"],
        "_mock": True,
    }


def build(_prompt: str, _plan: dict) -> str:
    return QUALITY_DASHBOARD


def edit(_original: str, current_html: str, _history: list, instruction: str) -> str:
    html = current_html or QUALITY_DASHBOARD
    if "深色" in instruction or "dark" in instruction.lower():
        html = html.replace("background:#f4f6fa;color:#1f2937", "background:#0f172a;color:#e5e7eb")
        html = html.replace("background:#fff;border-radius:12px", "background:#1e293b;border-radius:12px")
        html = html.replace("color:#6b7280", "color:#94a3b8")
        html = html.replace("border-bottom:1px solid #e5e7eb", "border-bottom:1px solid #334155")
        html = html.replace("border-bottom:1px solid #f3f4f6", "border-bottom:1px solid #334155")
    if "项目名称" in instruction:
        html = html.replace("<h1>接口自动化测试质量看板</h1>", "<h1>接口自动化测试质量看板 · QA Atlas</h1>")
    if "红色" in instruction:
        html = html.replace(".down{color:#dc2626}", ".down{color:#dc2626}.risk{background:#fef2f2;color:#dc2626;font-weight:600}")
    return html


def review(_html: str) -> dict:
    return {"valid": True, "issues": [], "score": 90, "_mock": True}
