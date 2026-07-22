"""Admin 模块 HTML 页面渲染（服务端渲染）。

所有动态值经 html.escape() 转义，防 XSS。
v0.4.0: 新增模型列表拉取、网关开关、QR 码、员工筛选/搜索/批量删除。
"""
from __future__ import annotations

from html import escape
from common.config import EnterpriseConfig

_BASE_CSS = """
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif;
       background: #f5f7fa; color: #333; }
.navbar { background: #2563eb; color: #fff; padding: 12px 24px; display: flex; gap: 24px; }
.navbar a { color: #fff; text-decoration: none; padding: 6px 12px; border-radius: 4px; }
.navbar a:hover, .navbar a.active { background: rgba(255,255,255,0.2); }
.container { max-width: 1100px; margin: 24px auto; padding: 0 16px; }
.card { background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
h1 { font-size: 20px; margin-bottom: 16px; }
h2 { font-size: 16px; margin-bottom: 12px; color: #555; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #e5e7eb; font-size: 14px; }
th { background: #f9fafb; font-weight: 600; }
.btn { display: inline-block; padding: 6px 16px; background: #2563eb; color: #fff;
       border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
.btn:hover { background: #1d4ed8; }
.btn-secondary { background: #6b7280; }
.btn-secondary:hover { background: #4b5563; }
.btn-success { background: #16a34a; }
.btn-success:hover { background: #15803d; }
.btn-danger { background: #dc2626; }
.btn-danger:hover { background: #b91c1c; }
.btn-sm { padding: 4px 10px; font-size: 12px; }
.form-group { margin-bottom: 12px; }
.form-group label { display: block; margin-bottom: 4px; font-size: 14px; font-weight: 500; }
.form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid #d1d5db;
       border-radius: 4px; font-size: 14px; }
.form-row { display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap; }
.form-row .form-group { flex: 1; min-width: 160px; }
.stat { display: inline-block; padding: 16px 24px; background: #eff6ff; border-radius: 8px;
        margin-right: 16px; }
.stat-num { font-size: 28px; font-weight: 700; color: #2563eb; }
.stat-label { font-size: 12px; color: #6b7280; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
.badge-confirmed { background: #d1fae5; color: #065f46; }
.badge-pending { background: #fef3c7; color: #92400e; }
.badge-bound { background: #d1fae5; color: #065f46; }
.badge-unbound { background: #f3f4f6; color: #6b7280; }

/* 开关 */
.switch { position: relative; display: inline-block; width: 48px; height: 24px; }
.switch input { opacity: 0; width: 0; height: 0; }
.slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
          background-color: #ccc; border-radius: 24px; transition: .3s; }
.slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px;
                 background-color: white; border-radius: 50%; transition: .3s; }
input:checked + .slider { background-color: #16a34a; }
input:checked + .slider:before { transform: translateX(24px); }

/* 筛选栏 */
.filter-bar { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
.filter-bar input, .filter-bar select { padding: 6px 10px; border: 1px solid #d1d5db;
       border-radius: 4px; font-size: 14px; }

/* QR 码 */
.qr-box { text-align: center; padding: 20px; }
.qr-box img { max-width: 240px; border: 1px solid #e5e7eb; border-radius: 8px; }
.qr-status { margin-top: 12px; font-size: 14px; color: #6b7280; }

/* 多选 */
.row-checkbox { width: 16px; height: 16px; cursor: pointer; }
.batch-bar { display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }
"""

_BASE_JS = """
function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
async function getJSON(url) { const r=await fetch(url); return r.json(); }
async function postJSON(url,body) { const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); return r.json(); }
"""


def _esc(value) -> str:
    return escape(str(value)) if value is not None else ""


def render_nav(active: str = "") -> str:
    items = [
        ("", "仪表盘"), ("llm", "LLM 配置"), ("database", "数据库加载"),
        ("stores", "门店与员工"), ("gateway", "微信网关绑定"), ("babies", "宝宝档案"),
    ]
    links = []
    for path, label in items:
        cls = ' class="active"' if path == active else ""
        links.append(f'<a href="/admin/{_esc(path)}"{cls}>{_esc(label)}</a>')
    return f'<div class="navbar">{"&nbsp;".join(links)}</div>'


def render_dashboard(cfg: EnterpriseConfig) -> str:
    eid = _esc(cfg.enterprise_id)
    llm_kind = _esc(cfg.llm.kind)
    emb_kind = _esc(cfg.embedding.kind)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>母婴 Agent 管理后台</title>
<style>{_BASE_CSS}</style></head><body>
{render_nav()}
<div class="container">
  <div class="card">
    <h1>仪表盘</h1>
    <div class="stat"><div class="stat-num">{eid}</div><div class="stat-label">企业 ID</div></div>
    <div class="stat"><div class="stat-num">{llm_kind}</div><div class="stat-label">LLM 模式</div></div>
    <div class="stat"><div class="stat-num">{emb_kind}</div><div class="stat-label">嵌入模型</div></div>
  </div>
  <div class="card">
    <h2>微信网关状态</h2>
    <div style="display:flex;align-items:center;gap:16px;">
      <label class="switch">
        <input type="checkbox" id="gw-toggle" onchange="toggleGateway(this)">
        <span class="slider"></span>
      </label>
      <span id="gw-status-text" style="font-size:14px;color:#6b7280;">加载中…</span>
    </div>
    <p style="color:#6b7280;font-size:12px;margin-top:8px;">切换开关以启动/停止微信消息网关</p>
  </div>
  <div class="card">
    <h2>快捷操作</h2>
    <p><a href="/admin/llm" class="btn">配置 LLM</a> &nbsp;
       <a href="/admin/database" class="btn">加载数据库</a> &nbsp;
       <a href="/admin/stores" class="btn">管理门店</a> &nbsp;
       <a href="/admin/gateway" class="btn">绑定微信</a> &nbsp;
       <a href="/admin/babies" class="btn">查看档案</a></p>
  </div>
</div>
<script>
{_BASE_JS}
async function loadGatewayStatus() {{
  const d = await getJSON('/api/gateway/status');
  const chk = document.getElementById('gw-toggle');
  const txt = document.getElementById('gw-status-text');
  chk.checked = d.running;
  txt.textContent = d.running ? '运行中' : '已停止';
  txt.style.color = d.running ? '#16a34a' : '#6b7280';
}}
async function toggleGateway(chk) {{
  const url = chk.checked ? '/api/gateway/start' : '/api/gateway/stop';
  const d = await fetch(url, {{method:'POST'}}).then(r=>r.json());
  if (d.status === 'error') {{ alert('操作失败: ' + (d.error||'')); chk.checked = !chk.checked; }}
  loadGatewayStatus();
}}
loadGatewayStatus();
setInterval(loadGatewayStatus, 5000);
</script>
</body></html>"""


def render_llm_page(cfg: EnterpriseConfig) -> str:
    kind = _esc(cfg.llm.kind)
    model = _esc(cfg.llm.model)
    base_url = _esc(cfg.llm.base_url or '')
    api_key_placeholder = '<已设置>' if cfg.llm.api_key else '未设置'
    temp = _esc(cfg.llm.temperature)
    max_tok = _esc(cfg.llm.max_tokens)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>LLM 配置</title>
<style>{_BASE_CSS}</style></head><body>
{render_nav("llm")}
<div class="container">
  <div class="card">
    <h1>模型配置</h1>
    <p style="color:#6b7280;margin-bottom:16px;">配置 LLM provider，支持 DeepSeek / OpenAI 兼容 API</p>
    <form id="llm-form">
      <div class="form-row">
        <div class="form-group">
          <label>Provider 类型</label>
          <select id="kind" onchange="onKindChange()">
            <option value="mock" {'selected' if cfg.llm.kind=='mock' else ''}>Mock（测试）</option>
            <option value="ollama" {'selected' if cfg.llm.kind=='ollama' else ''}>Ollama（端侧本地）</option>
            <option value="cloud" {'selected' if cfg.llm.kind=='cloud' else ''}>Cloud（云 API）</option>
          </select>
        </div>
        <div class="form-group">
          <label>预设厂商</label>
          <select id="preset" onchange="onPresetChange()">
            <option value="">自定义</option>
            <option value="deepseek">DeepSeek</option>
            <option value="openai">OpenAI</option>
            <option value="siliconflow">SiliconFlow</option>
          </select>
        </div>
      </div>
      <div class="form-group"><label>Base URL</label>
        <input id="base_url" value="{base_url}" placeholder="如 https://api.deepseek.com/v1"></div>
      <div class="form-row">
        <div class="form-group" style="flex:2">
          <label>API Key</label>
          <input id="api_key" type="password" value="" placeholder="{api_key_placeholder}">
        </div>
        <div class="form-group" style="flex:1">
          <label>&nbsp;</label>
          <button type="button" class="btn btn-secondary" onclick="fetchModels()" style="width:100%">拉取模型列表</button>
        </div>
      </div>
      <div id="model-select-box" class="form-group" style="display:none;">
        <label>选择模型</label>
        <select id="model_select" onchange="document.getElementById('model').value=this.value">
          <option value="">-- 请选择 --</option>
        </select>
        <span id="model-err" style="color:#dc2626;font-size:12px;"></span>
      </div>
      <div class="form-group"><label>模型名称（或手动填写）</label>
        <input id="model" value="{model}" placeholder="如 deepseek-chat"></div>
      <div class="form-row">
        <div class="form-group">
          <label>Temperature</label>
          <input id="temperature" type="number" step="0.1" value="{temp}"></div>
        <div class="form-group">
          <label>Max Tokens</label>
          <input id="max_tokens" type="number" value="{max_tok}"></div>
      </div>
      <button type="submit" class="btn">保存配置</button>
    </form>
    <div id="result" style="margin-top:12px;"></div>
  </div>
</div>
<script>
{_BASE_JS}
const PRESETS = {{
  deepseek:    {{ base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' }},
  openai:      {{ base_url: 'https://api.openai.com/v1',   model: 'gpt-4o' }},
  siliconflow: {{ base_url: 'https://api.siliconflow.cn/v1', model: 'deepseek-ai/DeepSeek-V3' }},
}};
function onPresetChange() {{
  const p = document.getElementById('preset').value;
  if (PRESETS[p]) {{
    document.getElementById('base_url').value = PRESETS[p].base_url;
    document.getElementById('model').value = PRESETS[p].model;
  }}
}}
function onKindChange() {{
  const k = document.getElementById('kind').value;
  if (k === 'ollama') {{
    document.getElementById('base_url').value = 'http://localhost:11434/v1';
    document.getElementById('model').value = 'llama3';
  }}
}}
async function fetchModels() {{
  const key = document.getElementById('api_key').value;
  const url = document.getElementById('base_url').value;
  const box = document.getElementById('model-select-box');
  const sel = document.getElementById('model_select');
  const err = document.getElementById('model-err');
  box.style.display = 'block'; err.textContent = '拉取中…';
  const d = await getJSON('/api/llm/models?api_key='+encodeURIComponent(key)+'&base_url='+encodeURIComponent(url));
  sel.innerHTML = '<option value="">-- 请选择 --</option>';
  if (d.error) {{ err.textContent = d.error; return; }}
  err.textContent = '';
  for (const m of d.models) {{
    const opt = document.createElement('option'); opt.value = m; opt.textContent = m;
    sel.appendChild(opt);
  }}
}}
document.getElementById('llm-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  const body = {{
    kind: document.getElementById('kind').value,
    model: document.getElementById('model').value,
    base_url: document.getElementById('base_url').value,
    api_key: document.getElementById('api_key').value,
    temperature: parseFloat(document.getElementById('temperature').value),
    max_tokens: parseInt(document.getElementById('max_tokens').value),
  }};
  const r = await fetch('/api/llm', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body)}});
  const data = await r.json();
  const card = document.createElement('div');
  card.className = 'card';
  card.style.background = '#d1fae5';
  card.textContent = data.message;
  const result = document.getElementById('result');
  result.innerHTML = '';
  result.appendChild(card);
}});
</script>
</body></html>"""


def render_database_page(cfg: EnterpriseConfig) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>数据库加载</title>
<style>{_BASE_CSS}</style></head><body>
{render_nav("database")}
<div class="container">
  <div class="card">
    <h1>数据库加载</h1>
    <div id="status"></div>
  </div>
  <div class="card">
    <h2>触发 bundle 扫描加载</h2>
    <p style="color:#6b7280;margin-bottom:8px;">扫描收件箱目录中的 dataproc bundle 并加载到知识库</p>
    <button onclick="scanInbox()" class="btn">扫描并加载</button>
    <div id="scan-result" style="margin-top:12px;"></div>
  </div>
  <div class="card">
    <h2>待确认商品</h2>
    <div id="pending"></div>
  </div>
</div>
<script>
{_BASE_JS}
async function loadStatus() {{
  const d = await getJSON('/api/database/status');
  document.getElementById('status').innerHTML = `
    <div class="stat"><div class="stat-num">${{esc(d.corpus_count)}}</div><div class="stat-label">语料条数</div></div>
    <div class="stat"><div class="stat-num">${{esc(d.products_milk)}}</div><div class="stat-label">奶粉商品</div></div>
    <div class="stat"><div class="stat-num">${{esc(d.products_nutrition)}}</div><div class="stat-label">营养品</div></div>
    <p style="margin-top:12px;">数据库路径: <code>${{esc(d.db_path)}}</code></p>
    <p>收件箱: <code>${{esc(d.bundle_inbox_dir||'未配置')}}</code> ${{d.inbox_exists ? '✓' : '✗'}}</p>
  `;
}}
async function loadPending() {{
  const d = await getJSON('/api/database/pending');
  if (!d.length) {{ document.getElementById('pending').innerHTML = '<p>无待确认商品</p>'; return; }}
  let html = '<table><tr><th>ID</th><th>名称</th><th>品牌</th><th>类型</th><th>操作</th></tr>';
  for (const p of d) {{
    html += `<tr><td>${{p.id}}</td><td>${{esc(p.name)}}</td><td>${{esc(p.brand)}}</td><td>${{esc(p.table)}}</td>
      <td><input id="val-${{p.id}}" placeholder="注册号/批准文号" style="width:160px;">
      <button onclick="confirmProduct(${{p.id}},'${{esc(p.table)}}')" class="btn">确认</button>
      <button onclick="deleteProduct(${{p.id}},'${{esc(p.table)}}')" class="btn btn-danger">删除</button></td></tr>`;
  }}
  html += '</table>';
  document.getElementById('pending').innerHTML = html;
}}
async function scanInbox() {{
  const r = await fetch('/api/database/scan', {{method:'POST'}});
  const d = await r.json();
  let html = `<div class="card" style="background:#d1fae5;">扫描完成</div>`;
  if (d.loaded) html += `<p>加载成功: ${{d.loaded.length}} 个 bundle</p>`;
  if (d.failed) html += `<p style="color:#dc2626;">失败: ${{d.failed.length}} 个</p>`;
  document.getElementById('scan-result').innerHTML = html;
  loadStatus(); loadPending();
}}
async function confirmProduct(id, table) {{
  const val = document.getElementById('val-'+id).value;
  await fetch(`/api/database/confirm?product_id=${{id}}&value=${{encodeURIComponent(val)}}&table=${{table}}`, {{method:'POST'}});
  loadPending();
}}
async function deleteProduct(id, table) {{
  if (!confirm('确认删除？')) return;
  await fetch(`/api/database/product?product_id=${{id}}&table=${{table}}`, {{method:'DELETE'}});
  loadPending();
}}
loadStatus(); loadPending();
</script>
</body></html>"""


def render_stores_page(cfg: EnterpriseConfig) -> str:
    eid = _esc(cfg.enterprise_id)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>门店与员工管理</title>
<style>{_BASE_CSS}</style></head><body>
{render_nav("stores")}
<div class="container">
  <div class="card">
    <h1>门店管理</h1>
    <div id="stores"></div>
  </div>
  <div class="card">
    <h2>新建门店</h2>
    <form id="store-form">
      <div class="form-group"><label>门店名称</label><input id="s-name" required></div>
      <button type="submit" class="btn">创建</button>
    </form>
  </div>
  <div class="card">
    <h1>员工管理</h1>
    <div class="filter-bar">
      <select id="f-store" onchange="loadEmployees()"><option value="">所有门店</option></select>
      <select id="f-bound" onchange="loadEmployees()">
        <option value="">全部状态</option>
        <option value="yes">已绑定网关</option>
        <option value="no">未绑定网关</option>
      </select>
      <input id="f-search" placeholder="搜索名字…" oninput="loadEmployees()">
    </div>
    <div class="batch-bar">
      <input type="checkbox" id="sel-all" onchange="toggleSelectAll()">
      <label for="sel-all" style="font-size:13px;">全选</label>
      <button class="btn btn-danger btn-sm" onclick="batchDelete()">批量删除</button>
    </div>
    <div id="employees"></div>
  </div>
  <div class="card">
    <h2>添加员工</h2>
    <form id="emp-form">
      <div class="form-row">
        <div class="form-group">
          <label>归属门店</label>
          <select id="e-store"><option value="">-- 请选择 --</option></select>
        </div>
        <div class="form-group">
          <label>员工 ID</label><input id="e-id" required placeholder="如 emp_001"></div>
        <div class="form-group">
          <label>员工姓名</label><input id="e-name" required></div>
      </div>
      <button type="submit" class="btn">添加</button>
    </form>
  </div>
</div>
<script>
{_BASE_JS}
let _stores = [];
let _employees = [];
async function loadStores() {{
  const d = await getJSON('/api/stores');
  _stores = d;
  let html = '<table><tr><th>企业 ID</th><th>门店名称</th><th>数据库</th></tr>';
  for (const s of d) html += `<tr><td>${{esc(s.enterprise_id)}}</td><td>${{esc(s.enterprise_name)}}</td><td><code>${{esc(s.db_path)}}</code></td></tr>`;
  html += '</table>';
  document.getElementById('stores').innerHTML = html;
  // 填充下拉框
  const opts = d.map(s=>`<option value="${{esc(s.enterprise_id)}}">${{esc(s.enterprise_name)}}</option>`).join('');
  document.getElementById('f-store').innerHTML = '<option value="">所有门店</option>' + opts;
  document.getElementById('e-store').innerHTML = '<option value="">-- 请选择 --</option>' + opts;
}}
async function loadEmployees() {{
  const store = document.getElementById('f-store').value;
  const bound = document.getElementById('f-bound').value;
  const search = document.getElementById('f-search').value;
  let url = '/api/employees';
  const q = [];
  if (store) q.push('store_id='+encodeURIComponent(store));
  if (bound) q.push('bound='+encodeURIComponent(bound));
  if (search) q.push('search='+encodeURIComponent(search));
  if (q.length) url += '?' + q.join('&');
  const d = await getJSON(url);
  _employees = d;
  if (!d.length) {{ document.getElementById('employees').innerHTML = '<p>暂无员工</p>'; return; }}
  let html = '<table><tr><th></th><th>ID</th><th>员工 ID</th><th>姓名</th><th>门店</th><th>绑定状态</th><th>操作</th></tr>';
  for (const e of d) {{
    const badge = e.bound ? '<span class="badge badge-bound">已绑定</span>' : '<span class="badge badge-unbound">未绑定</span>';
    const bindBtn = e.bound ? '' : `<a href="/admin/gateway?emp_id=${{e.id}}&emp_name=${{encodeURIComponent(e.employee_name)}}" class="btn btn-success btn-sm">绑定</a>`;
    const storeName = esc(_stores.find(s=>s.enterprise_id===e.store_id)?.enterprise_name || e.store_id || '-');
    html += `<tr>
      <td><input type="checkbox" class="row-checkbox" value="${{e.id}}"></td>
      <td>${{e.id}}</td><td>${{esc(e.employee_id)}}</td><td>${{esc(e.employee_name)}}</td>
      <td>${{storeName}}</td><td>${{badge}}</td>
      <td>${{bindBtn}} <button onclick="delEmp(${{e.id}})" class="btn btn-danger btn-sm">删除</button></td>
    </tr>`;
  }}
  html += '</table>';
  document.getElementById('employees').innerHTML = html;
}}
function toggleSelectAll() {{
  const checked = document.getElementById('sel-all').checked;
  document.querySelectorAll('.row-checkbox').forEach(c=>c.checked=checked);
}}
async function batchDelete() {{
  const ids = Array.from(document.querySelectorAll('.row-checkbox:checked')).map(c=>parseInt(c.value));
  if (!ids.length) {{ alert('请至少选择一项'); return; }}
  if (!confirm('确认删除选中的 ' + ids.length + ' 个员工？')) return;
  const d = await postJSON('/api/employees/batch-delete', {{ids}});
  if (d.status === 'ok') loadEmployees();
}}
document.getElementById('store-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await postJSON('/api/stores', {{enterprise_id:'{eid}', enterprise_name:document.getElementById('s-name').value, db_path:'instance.db'}});
  loadStores();
}});
document.getElementById('emp-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await postJSON('/api/employees', {{
    enterprise_id:'{eid}',
    store_id:document.getElementById('e-store').value,
    employee_id:document.getElementById('e-id').value,
    employee_name:document.getElementById('e-name').value,
  }});
  document.getElementById('e-id').value=''; document.getElementById('e-name').value='';
  loadEmployees();
}});
async function delEmp(id) {{ if(!confirm('确认删除？')) return; await fetch('/api/employees/'+id,{{method:'DELETE'}}); loadEmployees(); }}
loadStores(); loadEmployees();
</script>
</body></html>"""


def render_gateway_page(cfg: EnterpriseConfig) -> str:
    eid = _esc(cfg.enterprise_id)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>微信网关绑定</title>
<style>{_BASE_CSS}</style></head><body>
{render_nav("gateway")}
<div class="container">
  <div class="card">
    <h1>扫码绑定</h1>
    <p style="color:#6b7280;margin-bottom:16px;">使用微信扫描二维码，完成 bot 登录</p>
    <div class="qr-box">
      <div id="qr-img">点击获取二维码</div>
      <div class="qr-status" id="qr-status">未获取</div>
      <button class="btn" onclick="fetchQR()">获取二维码</button>
    </div>
  </div>
  <div class="card">
    <h2>手动绑定</h2>
    <form id="bind-form">
      <div class="form-row">
        <div class="form-group"><label>员工 ID</label><input id="b-emp" required></div>
        <div class="form-group"><label>微信名称</label><input id="b-name" placeholder="如：门店小李"></div>
        <div class="form-group"><label>iLink Bot Token</label><input id="b-token" type="password" required></div>
      </div>
      <button type="submit" class="btn">绑定</button>
    </form>
  </div>
  <div class="card">
    <h2>已绑定列表</h2>
    <div id="bindings"></div>
  </div>
</div>
<script>
{_BASE_JS}
// 从 URL 参数预填员工
(function() {{
  const p = new URLSearchParams(location.search);
  if (p.get('emp_name')) document.getElementById('b-name').value = p.get('emp_name');
}})();

let _qrTimer = null;
async function fetchQR() {{
  const d = await getJSON('/api/gateway/qrcode');
  const box = document.getElementById('qr-img');
  const st = document.getElementById('qr-status');
  if (d.qr_url) {{
    box.innerHTML = `<img src="${{esc(d.qr_url)}}" alt="QR Code">`;
    st.textContent = '等待扫码…';
    if (_qrTimer) clearInterval(_qrTimer);
    _qrTimer = setInterval(pollQRStatus, 3000);
  }} else {{
    box.textContent = '获取失败: ' + (d.error || '未知错误');
    st.textContent = '';
  }}
}}
async function pollQRStatus() {{
  const d = await getJSON('/api/gateway/qrcode/status');
  const st = document.getElementById('qr-status');
  st.textContent = '状态: ' + (d.status || '未知');
  if (d.status === 'confirmed' || d.status === 'authenticated') {{
    st.textContent = '扫码成功！';
    st.style.color = '#16a34a';
    if (_qrTimer) {{ clearInterval(_qrTimer); _qrTimer = null; }}
  }}
}}
async function loadBindings() {{
  const d = await getJSON('/api/gateway');
  if (!d.length) {{ document.getElementById('bindings').innerHTML = '<p>暂无绑定</p>'; return; }}
  let html = '<table><tr><th>ID</th><th>员工</th><th>微信名</th><th>Token</th><th>操作</th></tr>';
  for (const b of d) html += `<tr><td>${{b.id}}</td><td>${{esc(b.employee_id)}}</td><td>${{esc(b.wechat_name||'-')}}</td><td><code>${{esc(b.bot_token)}}</code></td>
    <td><button onclick="unbind(${{b.id}})" class="btn btn-danger btn-sm">解绑</button></td></tr>`;
  html += '</table>';
  document.getElementById('bindings').innerHTML = html;
}}
document.getElementById('bind-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await postJSON('/api/gateway', {{
    enterprise_id:'{eid}',
    employee_id:document.getElementById('b-emp').value,
    wechat_name:document.getElementById('b-name').value,
    bot_token:document.getElementById('b-token').value,
  }});
  document.getElementById('b-emp').value=''; document.getElementById('b-name').value=''; document.getElementById('b-token').value='';
  loadBindings();
}});
async function unbind(id) {{ if(!confirm('确认解绑？')) return; await fetch('/api/gateway/'+id,{{method:'DELETE'}}); loadBindings(); }}
loadBindings();
</script>
</body></html>"""


def render_babies_page(cfg: EnterpriseConfig) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>宝宝档案</title>
<style>{_BASE_CSS}</style></head><body>
{render_nav("babies")}
<div class="container">
  <div class="card">
    <h1>宝宝档案查看</h1>
    <p style="color:#6b7280;margin-bottom:16px;">当前企业下的宝宝档案（只读概览）</p>
    <div id="babies"></div>
  </div>
  <div class="card" id="detail" style="display:none;">
    <h2>档案详情</h2>
    <div id="detail-content"></div>
  </div>
</div>
<script>
{_BASE_JS}
async function loadBabies() {{
  const d = await getJSON('/api/babies');
  if (!d.length) {{ document.getElementById('babies').innerHTML = '<p>暂无宝宝档案</p>'; return; }}
  let html = '<table><tr><th>ID</th><th>姓名</th><th>月龄</th><th>性别</th><th>阶段</th><th>状态</th><th>操作</th></tr>';
  for (const b of d) {{
    const badge = b.status === 'confirmed' ? 'badge-confirmed' : 'badge-pending';
    html += `<tr><td>${{b.baby_id}}</td><td>${{esc(b.name)}}</td><td>${{esc(b.baby_age)}}</td><td>${{esc(b.gender)}}</td><td>${{esc(b.stage)}}</td>
      <td><span class="badge ${{badge}}">${{esc(b.status)}}</span></td>
      <td><button onclick="viewDetail(${{b.baby_id}})" class="btn btn-sm">查看</button></td></tr>`;
  }}
  html += '</table>';
  document.getElementById('babies').innerHTML = html;
}}
async function viewDetail(id) {{
  const d = await getJSON('/api/babies/'+id);
  let html = '<table>';
  for (const [k,v] of Object.entries(d)) {{
    let val = Array.isArray(v) ? v.map(esc).join(', ') : (v === null ? '-' : esc(v));
    html += `<tr><th>${{esc(k)}}</th><td>${{val}}</td></tr>`;
  }}
  html += '</table>';
  document.getElementById('detail-content').innerHTML = html;
  document.getElementById('detail').style.display = 'block';
}}
loadBabies();
</script>
</body></html>"""
