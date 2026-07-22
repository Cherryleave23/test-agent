"""Admin 模块 HTML 页面渲染（从 server.py 拆分）。

所有动态值经 html.escape() 转义，防 XSS。
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
.container { max-width: 960px; margin: 24px auto; padding: 0 16px; }
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
.btn-danger { background: #dc2626; }
.btn-danger:hover { background: #b91c1c; }
.form-group { margin-bottom: 12px; }
.form-group label { display: block; margin-bottom: 4px; font-size: 14px; font-weight: 500; }
.form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid #d1d5db;
       border-radius: 4px; font-size: 14px; }
.stat { display: inline-block; padding: 16px 24px; background: #eff6ff; border-radius: 8px;
        margin-right: 16px; }
.stat-num { font-size: 28px; font-weight: 700; color: #2563eb; }
.stat-label { font-size: 12px; color: #6b7280; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; }
.badge-confirmed { background: #d1fae5; color: #065f46; }
.badge-pending { background: #fef3c7; color: #92400e; }
"""


def _esc(value) -> str:
    """HTML 转义。"""
    return escape(str(value)) if value is not None else ""


def render_nav(active: str = "") -> str:
    items = [
        ("", "仪表盘"), ("llm", "LLM 选择"), ("database", "数据库加载"),
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
    <h2>快捷操作</h2>
    <p><a href="/admin/llm" class="btn">配置 LLM</a> &nbsp;
       <a href="/admin/database" class="btn">加载数据库</a> &nbsp;
       <a href="/admin/stores" class="btn">管理门店</a> &nbsp;
       <a href="/admin/gateway" class="btn">绑定微信</a> &nbsp;
       <a href="/admin/babies" class="btn">查看档案</a></p>
  </div>
</div>
</body></html>"""


def render_llm_page(cfg: EnterpriseConfig) -> str:
    kind = _esc(cfg.llm.kind)
    model = _esc(cfg.llm.model)
    base_url = _esc(cfg.llm.base_url or '')
    api_key_placeholder = '<已设置>' if cfg.llm.api_key else '未设置'
    temp = _esc(cfg.llm.temperature)
    max_tok = _esc(cfg.llm.max_tokens)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>LLM 选择</title>
<style>{_BASE_CSS}</style></head><body>
{render_nav("llm")}
<div class="container">
  <div class="card">
    <h1>LLM 配置</h1>
    <p style="color:#6b7280;margin-bottom:16px;">当前配置（修改后需重启 agent 生效）</p>
    <form id="llm-form">
      <div class="form-group">
        <label>Provider 类型</label>
        <select id="kind">
          <option value="mock" {'selected' if cfg.llm.kind=='mock' else ''}>Mock（测试）</option>
          <option value="ollama" {'selected' if cfg.llm.kind=='ollama' else ''}>Ollama（端侧本地）</option>
          <option value="cloud" {'selected' if cfg.llm.kind=='cloud' else ''}>Cloud（云 API）</option>
        </select>
      </div>
      <div class="form-group"><label>模型名称</label>
        <input id="model" value="{model}"></div>
      <div class="form-group"><label>Base URL</label>
        <input id="base_url" value="{base_url}"></div>
      <div class="form-group"><label>API Key</label>
        <input id="api_key" type="password" value=""
               placeholder="{api_key_placeholder}"></div>
      <div class="form-group"><label>Temperature</label>
        <input id="temperature" type="number" step="0.1" value="{temp}"></div>
      <div class="form-group"><label>Max Tokens</label>
        <input id="max_tokens" type="number" value="{max_tok}"></div>
      <button type="submit" class="btn">保存配置</button>
    </form>
    <div id="result" style="margin-top:12px;"></div>
  </div>
</div>
<script>
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
  document.getElementById('result').innerHTML = '<div class="card" style="background:#d1fae5;">' + data.message + '</div>';
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
async function loadStatus() {{
  const r = await fetch('/api/database/status');
  const d = await r.json();
  const dbPath = document.createElement('span');
  dbPath.textContent = d.db_path || '';
  const inboxDir = document.createElement('span');
  inboxDir.textContent = d.bundle_inbox_dir || '未配置';
  document.getElementById('status').innerHTML = `
    <div class="stat"><div class="stat-num">${{d.corpus_count}}</div><div class="stat-label">语料条数</div></div>
    <div class="stat"><div class="stat-num">${{d.products_milk}}</div><div class="stat-label">奶粉商品</div></div>
    <div class="stat"><div class="stat-num">${{d.products_nutrition}}</div><div class="stat-label">营养品</div></div>
    <p style="margin-top:12px;">数据库路径: <code></code></p>
    <p>收件箱: <code></code> ${{d.inbox_exists ? '✓' : '✗'}}</p>
  `;
  document.querySelector('#status code:nth-of-type(1)').textContent = d.db_path || '';
  document.querySelector('#status code:nth-of-type(2)').textContent = d.bundle_inbox_dir || '未配置';
}}
async function loadPending() {{
  const r = await fetch('/api/database/pending');
  const d = await r.json();
  if (!d.length) {{ document.getElementById('pending').innerHTML = '<p>无待确认商品</p>'; return; }}
  let html = '<table><tr><th>ID</th><th>名称</th><th>品牌</th><th>类型</th><th>操作</th></tr>';
  for (const p of d) {{
    const safeName = (p.name || '').replace(/</g, '&lt;');
    const safeBrand = (p.brand || '').replace(/</g, '&lt;');
    html += `<tr><td>${{p.id}}</td><td>${{safeName}}</td><td>${{safeBrand}}</td><td>${{p.table}}</td>
      <td><input id="val-${{p.id}}" placeholder="注册号/批准文号" style="width:160px;">
      <button onclick="confirmProduct(${{p.id}},'${{p.table}}')" class="btn">确认</button>
      <button onclick="deleteProduct(${{p.id}},'${{p.table}}')" class="btn btn-danger">删除</button></td></tr>`;
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
      <div class="form-group"><label>企业 ID</label><input id="s-eid" required></div>
      <div class="form-group"><label>企业名称</label><input id="s-name" required></div>
      <div class="form-group"><label>数据库路径</label><input id="s-db" value="instance.db"></div>
      <button type="submit" class="btn">创建</button>
    </form>
  </div>
  <div class="card">
    <h2>员工管理</h2>
    <div id="employees"></div>
  </div>
  <div class="card">
    <h2>添加员工</h2>
    <form id="emp-form">
      <div class="form-group"><label>企业 ID</label><input id="e-eid" value="{eid}" required></div>
      <div class="form-group"><label>员工 ID</label><input id="e-id" required></div>
      <div class="form-group"><label>员工姓名</label><input id="e-name" required></div>
      <button type="submit" class="btn">添加</button>
    </form>
  </div>
</div>
<script>
function esc(s) {{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
async function loadStores() {{
  const r = await fetch('/api/stores'); const d = await r.json();
  let html = '<table><tr><th>企业 ID</th><th>名称</th><th>数据库</th></tr>';
  for (const s of d) html += `<tr><td>${{esc(s.enterprise_id)}}</td><td>${{esc(s.enterprise_name)}}</td><td><code>${{esc(s.db_path)}}</code></td></tr>`;
  html += '</table>'; document.getElementById('stores').innerHTML = html;
}}
async function loadEmployees() {{
  const r = await fetch('/api/employees'); const d = await r.json();
  let html = '<table><tr><th>ID</th><th>企业</th><th>员工 ID</th><th>姓名</th><th>微信</th><th>操作</th></tr>';
  for (const e of d) html += `<tr><td>${{e.id}}</td><td>${{esc(e.enterprise_id)}}</td><td>${{esc(e.employee_id)}}</td><td>${{esc(e.employee_name)}}</td><td>${{esc(e.wechat_name||'-')}}</td>
    <td><button onclick="delEmp(${{e.id}})" class="btn btn-danger">删除</button></td></tr>`;
  html += '</table>'; document.getElementById('employees').innerHTML = html;
}}
document.getElementById('store-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await fetch('/api/stores', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{enterprise_id:document.getElementById('s-eid').value, enterprise_name:document.getElementById('s-name').value, db_path:document.getElementById('s-db').value}})}});
  loadStores();
}});
document.getElementById('emp-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await fetch('/api/employees', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{enterprise_id:document.getElementById('e-eid').value, employee_id:document.getElementById('e-id').value, employee_name:document.getElementById('e-name').value}})}});
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
    <h1>微信网关绑定</h1>
    <p style="color:#6b7280;margin-bottom:16px;">将员工的 iLink Bot Token 绑定到系统，实现微信消息接入</p>
    <div id="bindings"></div>
  </div>
  <div class="card">
    <h2>绑定微信网关</h2>
    <form id="bind-form">
      <div class="form-group"><label>企业 ID</label><input id="b-eid" value="{eid}" required></div>
      <div class="form-group"><label>员工 ID</label><input id="b-emp" required></div>
      <div class="form-group"><label>微信名称</label><input id="b-name" placeholder="如：门店小李"></div>
      <div class="form-group"><label>iLink Bot Token</label><input id="b-token" type="password" required></div>
      <button type="submit" class="btn">绑定</button>
    </form>
  </div>
</div>
<script>
function esc(s) {{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
async function loadBindings() {{
  const r = await fetch('/api/gateway'); const d = await r.json();
  let html = '<table><tr><th>ID</th><th>企业</th><th>员工</th><th>微信名</th><th>Token</th><th>操作</th></tr>';
  for (const b of d) html += `<tr><td>${{b.id}}</td><td>${{esc(b.enterprise_id)}}</td><td>${{esc(b.employee_id)}}</td><td>${{esc(b.wechat_name||'-')}}</td><td><code>${{esc(b.bot_token)}}</code></td>
    <td><button onclick="unbind(${{b.id}})" class="btn btn-danger">解绑</button></td></tr>`;
  html += '</table>'; document.getElementById('bindings').innerHTML = html;
}}
document.getElementById('bind-form').addEventListener('submit', async (e) => {{
  e.preventDefault();
  await fetch('/api/gateway', {{method:'POST', headers:{{'Content-Type':'application/json'}},
    body: JSON.stringify({{enterprise_id:document.getElementById('b-eid').value, employee_id:document.getElementById('b-emp').value, wechat_name:document.getElementById('b-name').value, bot_token:document.getElementById('b-token').value}})}});
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
function esc(s) {{ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }}
async function loadBabies() {{
  const r = await fetch('/api/babies'); const d = await r.json();
  let html = '<table><tr><th>ID</th><th>姓名</th><th>月龄</th><th>性别</th><th>阶段</th><th>状态</th><th>操作</th></tr>';
  for (const b of d) {{
    const badge = b.status === 'confirmed' ? 'badge-confirmed' : 'badge-pending';
    html += `<tr><td>${{b.baby_id}}</td><td>${{esc(b.name)}}</td><td>${{esc(b.baby_age)}}</td><td>${{esc(b.gender)}}</td><td>${{esc(b.stage)}}</td>
      <td><span class="badge ${{badge}}">${{esc(b.status)}}</span></td>
      <td><button onclick="viewDetail(${{b.baby_id}})" class="btn">查看</button></td></tr>`;
  }}
  html += '</table>'; document.getElementById('babies').innerHTML = html;
}}
async function viewDetail(id) {{
  const r = await fetch('/api/babies/'+id); const d = await r.json();
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
