"""
perdura_station.py — the Station: a live local dashboard for the mind.

    python perdura.py ui                       # http://127.0.0.1:8800
    python perdura.py ui --port 9000 --graph /path/to/graph.json

Serves a single-page operator console over the graph file:
- live force-directed graph (polls every 2s — watch a session land in
  real time; contradiction edges in red, superseded nodes faded)
- questions ranked by contention, with claim counts
- click any node for the inspector (text, attribution, edges, lineage)
- the conversation feed (who wrote what, challenges called out)
- per-model track records (this is an operator console for humans;
  workers connect via the MCP station, which keeps attribution hidden)

Zero dependencies: stdlib http.server reading the graph file fresh per
request — Graph.save() is atomic, so reads are always consistent.
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from perdura import Graph
from perdura_track import track_records


def payload(graph_path: str) -> dict:
    """Everything the dashboard needs, derived fresh from the graph file."""
    if not os.path.isfile(graph_path):
        return {"nodes": [], "edges": [], "questions": [], "track": {},
                "contention": 0.0, "log_tail": [], "exists": False}
    g = Graph(graph_path)
    questions = []
    for q in sorted(g.live_nodes(), key=lambda n: n.created_at):
        if q.type != "question":
            continue
        hood = g.neighborhood(q.id)
        questions.append({
            "id": q.id, "text": q.text, "status": q.status,
            "contention": g.contention(hood),
            "claims": sum(1 for n in g.live_nodes()
                          if n.id in hood and n.type == "claim"),
        })
    questions.sort(key=lambda r: -r["contention"])
    # Conductor panel: a read-only routing preview. What the contention
    # policy would do right now — local by default, frontier where the
    # graph disagrees with itself past the threshold. Derived from the
    # graph + the router's cost model; no ledger is persisted.
    from perdura_router import (DEFAULT_COSTS, DEFAULT_TIERS,
                                DEFAULT_ESCALATE_AT)
    routing = {
        "threshold": DEFAULT_ESCALATE_AT,
        "registry": [{"name": k, "tier": DEFAULT_TIERS.get(k, "local"),
                      "cost": DEFAULT_COSTS[k]} for k in DEFAULT_COSTS],
        "preview": [{"id": q["id"], "text": q["text"],
                     "contention": q["contention"],
                     "route": ("frontier" if q["contention"] >=
                               DEFAULT_ESCALATE_AT else "local")}
                    for q in questions],
    }
    return {
        "exists": True,
        "routing": routing,
        "nodes": [{"id": n.id, "type": n.type, "text": n.text,
                   "confidence": n.confidence, "by": n.created_by,
                   "tags": n.domain_tags or [], "t": n.created_at,
                   "status": n.status,
                   "superseded": n.superseded_by is not None}
                  for n in g.nodes.values()],
        "edges": [{"id": e.id, "type": e.type, "src": e.src, "dst": e.dst,
                   "by": e.created_by, "t": e.created_at}
                  for e in g.edges.values()],
        "questions": questions,
        "track": track_records(g),
        "contention": g.contention(),
        "log_tail": g.log[-12:],
    }


class _Handler(BaseHTTPRequestHandler):
    graph_path = "perdura_graph.json"

    def log_message(self, *args):           # keep the terminal quiet
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/graph":
            try:
                body = json.dumps(payload(self.graph_path)).encode()
            except Exception as e:                # mid-write or corrupt file
                self.send_error(500, f"graph read failed: {e}")
                return
            self._send(body, "application/json")
        else:
            self.send_error(404)


def serve(graph_path: str, port: int = 8800, host: str = "127.0.0.1"):
    handler = type("Handler", (_Handler,), {"graph_path": graph_path})
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Perdura Station: http://{host}:{port}  "
          f"(graph: {os.path.abspath(graph_path)}, Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStation closed.")
    finally:
        httpd.server_close()


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Perdura Station</title>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--ink:#0a0e1f;--ink2:#0e1430;--ink3:#131a3d;--paper:#f0f3fa;--muted:#97a3c4;
--faint:#5b6688;--cyan:#2dd9ff;--amber:#ffb454;--rose:#ff5d8f;--green:#3ddc97;
--line:rgba(151,163,196,.16)}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--ink);color:var(--paper);font:15px/1.55 "IBM Plex Sans",sans-serif;
height:100vh;display:flex;flex-direction:column;overflow:hidden}
header{display:flex;align-items:center;gap:22px;padding:0 20px;height:54px;
border-bottom:1px solid var(--line);background:var(--ink2);flex:none}
.wordmark{font-family:Fraunces,serif;font-weight:600;font-size:1.15rem}
.wordmark i{color:var(--cyan);font-style:normal}
.stat{font:12px "IBM Plex Mono",monospace;color:var(--muted)}
.stat b{color:var(--paper);font-weight:500}
.stat.hot b{color:var(--rose)}
#live{width:8px;height:8px;border-radius:50%;background:var(--green);
animation:pulse 2s infinite}
#live.err{background:var(--rose);animation:none}
@keyframes pulse{50%{opacity:.25}}
main{flex:1;display:flex;min-height:0}
#stage{flex:1;position:relative;min-width:0}
canvas{display:block;cursor:grab}
aside{width:400px;flex:none;border-left:1px solid var(--line);background:var(--ink2);
display:flex;flex-direction:column;min-height:0}
nav{display:flex;border-bottom:1px solid var(--line);flex:none}
nav button{flex:1;background:none;border:none;color:var(--faint);padding:11px 0;
font:11px "IBM Plex Mono",monospace;letter-spacing:.12em;cursor:pointer;
border-bottom:2px solid transparent;text-transform:uppercase}
nav button.on{color:var(--cyan);border-bottom-color:var(--cyan)}
#panel{flex:1;overflow-y:auto;padding:16px 18px}
.q{padding:11px 12px;border:1px solid var(--line);border-radius:9px;margin-bottom:9px;
cursor:pointer;transition:border-color .15s}
.q:hover,.q.sel{border-color:rgba(45,217,255,.5)}
.q .qt{font-size:.86rem;color:var(--paper)}
.q .qm{font:11px "IBM Plex Mono",monospace;color:var(--faint);margin-top:5px}
.q .qm .hot{color:var(--rose)}
.q .qm .ok{color:var(--green)}
.fe{margin-bottom:13px}
.fe .fh{font:12px "IBM Plex Mono",monospace}
.fe .fb{font-size:.84rem;color:var(--muted);margin-top:2px}
.kv{font:12.5px "IBM Plex Mono",monospace;color:var(--faint);margin:3px 0}
.kv b{color:var(--muted);font-weight:500}
#insp .text{font-size:.92rem;color:var(--paper);background:var(--ink3);
padding:11px 13px;border-radius:9px;margin:10px 0}
.edge-row{font:12px "IBM Plex Mono",monospace;color:var(--muted);margin:4px 0;
cursor:pointer}
.edge-row:hover{color:var(--cyan)}
.tk{margin-bottom:14px}
.tk .tw{font:13px "IBM Plex Mono",monospace;color:var(--paper)}
.tk .bar{height:6px;background:var(--ink3);border-radius:3px;margin:6px 0;overflow:hidden}
.tk .bar i{display:block;height:100%;border-radius:3px}
.tk .td{font:11px "IBM Plex Mono",monospace;color:var(--faint)}
.empty{color:var(--faint);font-size:.88rem;padding:30px 8px;text-align:center}
h3{font-family:Fraunces,serif;font-size:1.02rem;font-weight:600;margin-bottom:10px}
.tag{display:inline-block;font:10px "IBM Plex Mono",monospace;border:1px solid var(--line);
border-radius:99px;padding:1px 8px;margin:2px 3px 2px 0;color:var(--muted)}
</style></head><body>
<header>
  <span class="wordmark">perdura<i>.</i>station</span>
  <span id="live" title="polling"></span>
  <span class="stat">nodes <b id="s-n">0</b></span>
  <span class="stat">edges <b id="s-e">0</b></span>
  <span class="stat hot">contradicts <b id="s-c">0</b></span>
  <span class="stat">contention <b id="s-x">0.000</b></span>
  <span class="stat" style="margin-left:auto" id="s-path"></span>
</header>
<main>
  <div id="stage"><canvas id="c"></canvas></div>
  <aside>
    <nav>
      <button data-tab="questions" class="on">Questions</button>
      <button data-tab="feed">Feed</button>
      <button data-tab="insp">Inspector</button>
      <button data-tab="track">Track</button>
      <button data-tab="cond">Conductor</button>
    </nav>
    <div id="panel"></div>
  </aside>
</main>
<script>
const COLORS={question:"#2dd9ff",claim:"#f0f3fa",evidence:"#3ddc97",
decision:"#ffb454",rejected:"#ff5d8f"};
const WCOL={claude:"#ffb454",gemini:"#2dd9ff",qwen:"#3ddc97",user:"#5b6688"};
const wcol=w=>WCOL[w]||"#97a3c4";
let G={nodes:[],edges:[]},sim={},tab="questions",sel=null,hl=new Set(),
cam={x:0,y:0,z:1},drag=null,hover=null,firstLoad=true;

const cv=document.getElementById("c"),ctx=cv.getContext("2d"),
stage=document.getElementById("stage");
function resize(){cv.width=stage.clientWidth;cv.height=stage.clientHeight}
resize();addEventListener("resize",resize);

// ── data polling ──────────────────────────────────────────────
async function poll(){
  try{
    const r=await fetch("/api/graph");G=await r.json();
    document.getElementById("live").classList.remove("err");
    for(const n of G.nodes){
      if(!sim[n.id]){
        // spawn near a linked neighbor if one is placed already
        let px=cv.width/2,py=cv.height/2;
        for(const e of G.edges){
          const o=e.src===n.id?e.dst:e.dst===n.id?e.src:null;
          if(o&&sim[o]){px=sim[o].x;py=sim[o].y;break}}
        sim[n.id]={x:px+(Math.random()-.5)*(firstLoad?420:60),
                   y:py+(Math.random()-.5)*(firstLoad?320:60),vx:0,vy:0,
                   born:firstLoad?0:performance.now()};
      }}
    firstLoad=false;
    document.getElementById("s-n").textContent=G.nodes.length;
    document.getElementById("s-e").textContent=G.edges.length;
    document.getElementById("s-c").textContent=
      G.edges.filter(e=>e.type==="contradicts").length;
    document.getElementById("s-x").textContent=(G.contention||0).toFixed(3);
    render();
  }catch(e){document.getElementById("live").classList.add("err")}
}
setInterval(poll,2000);poll();

// ── physics + draw loop ───────────────────────────────────────
function step(){
  const ids=G.nodes.map(n=>n.id).filter(i=>sim[i]);
  for(const i of ids){sim[i].fx=0;sim[i].fy=0}
  for(let a=0;a<ids.length;a++)for(let b=a+1;b<ids.length;b++){
    const p=sim[ids[a]],q=sim[ids[b]];
    let dx=p.x-q.x,dy=p.y-q.y,d2=dx*dx+dy*dy+.01,d=Math.sqrt(d2),f=2400/d2;
    p.fx+=dx/d*f;p.fy+=dy/d*f;q.fx-=dx/d*f;q.fy-=dy/d*f}
  for(const e of G.edges){
    const p=sim[e.src],q=sim[e.dst];if(!p||!q)continue;
    let dx=q.x-p.x,dy=q.y-p.y,d=Math.sqrt(dx*dx+dy*dy)+.01,f=.0012*(d-105);
    p.fx+=dx*f;p.fy+=dy*f;q.fx-=dx*f;q.fy-=dy*f}
  for(const i of ids){const s=sim[i];
    s.fx+=(cv.width/2-s.x)*.0015;s.fy+=(cv.height/2-s.y)*.0015;
    s.vx=(s.vx+s.fx)*.8;s.vy=(s.vy+s.fy)*.8;
    s.x+=Math.max(-10,Math.min(10,s.vx));s.y+=Math.max(-10,Math.min(10,s.vy))}
}
function render(){if(tab==="questions")drawQuestions();
  else if(tab==="feed")drawFeed();else if(tab==="track")drawTrack();
  else if(tab==="cond")drawConductor();else drawInspector()}
function draw(){
  step();
  ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,cv.width,cv.height);
  ctx.setTransform(cam.z,0,0,cam.z,cam.x,cam.y);
  const dim=hl.size>0;
  for(const e of G.edges){
    const p=sim[e.src],q=sim[e.dst];if(!p||!q)continue;
    const hot=e.type==="contradicts",
      faded=dim&&!(hl.has(e.src)&&hl.has(e.dst));
    ctx.globalAlpha=faded?.12:1;
    ctx.strokeStyle=hot?"#ff5d8f":"rgba(151,163,196,.3)";
    ctx.lineWidth=(hot?1.8:1)/cam.z;
    ctx.setLineDash(hot?[5,4]:[]);
    ctx.beginPath();ctx.moveTo(p.x,p.y);ctx.lineTo(q.x,q.y);ctx.stroke()}
  ctx.setLineDash([]);
  const now=performance.now();
  for(const n of G.nodes){const s=sim[n.id];if(!s)continue;
    const faded=dim&&!hl.has(n.id);
    ctx.globalAlpha=(n.superseded?.3:1)*(faded?.15:1);
    let r=n.type==="question"?8:5.5;
    const age=now-s.born;if(age<900)r+=4*(1-age/900);   // birth flash
    ctx.beginPath();ctx.arc(s.x,s.y,r,0,7);
    if(n.type==="question"){ctx.strokeStyle="#2dd9ff";ctx.lineWidth=2.4/cam.z;
      ctx.stroke()}
    else{ctx.fillStyle=COLORS[n.type]||"#97a3c4";ctx.fill()}
    if(n.id===sel||n.id===hover){ctx.strokeStyle="#fff";
      ctx.lineWidth=1.4/cam.z;ctx.stroke()}}
  ctx.globalAlpha=1;
  requestAnimationFrame(draw)}
requestAnimationFrame(draw);

// ── interactions ──────────────────────────────────────────────
const world=(px,py)=>({x:(px-cam.x)/cam.z,y:(py-cam.y)/cam.z});
function pick(px,py){const w=world(px,py);
  for(let i=G.nodes.length-1;i>=0;i--){const n=G.nodes[i],s=sim[n.id];
    if(!s)continue;const dx=s.x-w.x,dy=s.y-w.y;
    if(dx*dx+dy*dy<144)return n.id}return null}
cv.addEventListener("mousedown",e=>{drag={x:e.offsetX,y:e.offsetY,moved:false};
  cv.style.cursor="grabbing"});
addEventListener("mouseup",e=>{
  if(drag&&!drag.moved){const id=pick(drag.x,drag.y);
    if(id){sel=id;setHL(id);tab="insp";syncTabs();render()}
    else{sel=null;hl.clear();render()}}
  drag=null;cv.style.cursor="grab"});
cv.addEventListener("mousemove",e=>{
  if(drag){cam.x+=e.offsetX-drag.x;cam.y+=e.offsetY-drag.y;
    drag.x=e.offsetX;drag.y=e.offsetY;drag.moved=true;return}
  hover=pick(e.offsetX,e.offsetY);
  cv.title=hover?(G.nodes.find(n=>n.id===hover)||{}).text||"":""});
cv.addEventListener("wheel",e=>{e.preventDefault();
  const k=e.deltaY<0?1.12:.89,w=world(e.offsetX,e.offsetY);
  cam.z=Math.max(.25,Math.min(4,cam.z*k));
  cam.x=e.offsetX-w.x*cam.z;cam.y=e.offsetY-w.y*cam.z},{passive:false});

function setHL(id){hl=new Set([id]);let f=new Set([id]);
  for(let h=0;h<2;h++){const nx=new Set();
    for(const e of G.edges){
      if(f.has(e.src)&&!hl.has(e.dst))nx.add(e.dst);
      if(f.has(e.dst)&&!hl.has(e.src))nx.add(e.src)}
    nx.forEach(i=>hl.add(i));f=nx}}

// ── panel tabs ────────────────────────────────────────────────
const panel=document.getElementById("panel"),
esc=s=>{const d=document.createElement("i");d.textContent=s||"";return d.innerHTML};
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  tab=b.dataset.tab;syncTabs();render()});
function syncTabs(){document.querySelectorAll("nav button").forEach(b=>
  b.classList.toggle("on",b.dataset.tab===tab))}

function drawQuestions(){
  if(!G.questions||!G.questions.length){
    panel.innerHTML='<div class="empty">No questions yet — seed one with<br>perdura.py new "…"</div>';return}
  panel.innerHTML="<h3>Open questions by contention</h3>"+G.questions.map(q=>
    `<div class="q${q.id===sel?" sel":""}" data-id="${q.id}">
      <div class="qt">${esc(q.text)}</div>
      <div class="qm"><span class="${q.contention>0?"hot":""}">contention ${q.contention.toFixed(3)}</span>
       · ${q.claims} claims · ${q.status==="resolved"?'<span class="ok">resolved</span>':"open"}</div></div>`).join("");
  panel.querySelectorAll(".q").forEach(el=>el.onclick=()=>{
    sel=el.dataset.id;setHL(sel);drawQuestions()})}

function drawFeed(){
  const items=[...G.nodes.map(n=>({t:n.t,n})),
    ...G.edges.filter(e=>e.type==="contradicts").map(e=>({t:e.t,e}))]
    .sort((a,b)=>b.t-a.t).slice(0,40);
  if(!items.length){panel.innerHTML='<div class="empty">Nothing yet.</div>';return}
  panel.innerHTML="<h3>The conversation</h3>"+items.map(it=>{
    if(it.e){const tgt=G.nodes.find(n=>n.id===it.e.dst)||{};
      return `<div class="fe"><div class="fh" style="color:#ff5d8f">${esc(it.e.by)} ⚡ contradicts</div>
        <div class="fb">"${esc((tgt.text||"").slice(0,110))}…"</div></div>`}
    const n=it.n;
    return `<div class="fe"><div class="fh" style="color:${wcol(n.by)}">${esc(n.by)} · ${n.type}${n.type!=="question"?" · "+n.confidence.toFixed(2):""}</div>
      <div class="fb">${esc(n.text.slice(0,150))}${n.text.length>150?"…":""}</div></div>`}).join("")}

function drawInspector(){
  const n=G.nodes.find(x=>x.id===sel);
  if(!n){panel.innerHTML='<div class="empty">Click a node in the graph.</div>';return}
  const edges=G.edges.filter(e=>e.src===n.id||e.dst===n.id);
  panel.innerHTML=`<h3 style="color:${COLORS[n.type]||"#97a3c4"}">${n.type}${n.superseded?" · superseded":""}</h3>
    <div class="text">${esc(n.text)}</div>
    <div class="kv">id <b>${n.id}</b></div>
    <div class="kv">by <b style="color:${wcol(n.by)}">${esc(n.by)}</b> · confidence <b>${n.confidence.toFixed(2)}</b></div>
    <div>${(n.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join("")}</div>
    <h3 style="margin-top:18px">${edges.length} edges</h3>`+
    edges.map(e=>{const out=e.src===n.id,o=out?e.dst:e.src,
      on=G.nodes.find(x=>x.id===o)||{};
      return `<div class="edge-row" data-id="${o}">${out?"→":"←"} [${e.type}] ${esc((on.text||o).slice(0,52))}…</div>`}).join("");
  panel.querySelectorAll(".edge-row").forEach(el=>el.onclick=()=>{
    sel=el.dataset.id;setHL(sel);drawInspector()})}

function drawTrack(){
  const ws=Object.entries(G.track||{}).sort((a,b)=>b[1].reliability-a[1].reliability);
  if(!ws.length){panel.innerHTML='<div class="empty">No attributed claims yet.</div>';return}
  panel.innerHTML="<h3>Track records (0.5 = no evidence)</h3>"+ws.map(([w,r])=>{
    const c=r.reliability>=.55?"#3ddc97":r.reliability<=.45?"#ff5d8f":"#97a3c4";
    return `<div class="tk"><div class="tw" style="color:${wcol(w)}">${esc(w)} — ${r.reliability.toFixed(3)}</div>
      <div class="bar"><i style="width:${(r.reliability*100).toFixed(0)}%;background:${c}"></i></div>
      <div class="td">${r.claims} claims · +${r.good.toFixed(1)} good · −${r.bad.toFixed(1)} bad</div></div>`}).join("")}

function drawConductor(){
  const R=G.routing;
  if(!R){panel.innerHTML='<div class="empty">Router preview unavailable.</div>';return}
  let h="<h3>Conductor — routing preview</h3>";
  h+=`<div class="td" style="margin-bottom:12px">Local labor by default; a frontier worker is summoned where a question's contention reaches ${R.threshold}.</div>`;
  h+="<div class='tk'><div class='tw'>Model registry</div>"+R.registry.map(m=>
    `<div class="td">${esc(m.name)} · <span style="color:${m.tier==="frontier"?"#ffb454":"#3ddc97"}">${m.tier}</span> · cost ${m.cost}</div>`).join("")+"</div>";
  if(!R.preview.length){h+='<div class="empty">No open questions to route.</div>';panel.innerHTML=h;return}
  h+=R.preview.map(p=>{
    const front=p.route==="frontier",c=front?"#ffb454":"#3ddc97";
    return `<div class="tk"><div class="tw">${esc(p.text)}</div>
      <div class="bar"><i style="width:${Math.min(100,p.contention*100).toFixed(0)}%;background:${c}"></i></div>
      <div class="td">contention ${p.contention.toFixed(3)} · would route to <span style="color:${c}">${p.route}</span></div></div>`}).join("");
  panel.innerHTML=h}

document.getElementById("s-path").textContent=location.host;
</script></body></html>
"""
