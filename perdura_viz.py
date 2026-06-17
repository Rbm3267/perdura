"""
perdura_viz.py — Phase 1.5 mind-map visualization.

Renders the graph as a self-contained HTML file: canvas force layout, nodes
colored by type, contradicts edges in red (the contention you can see),
superseded nodes faded. No external dependencies; open the file anywhere.

    python perdura.py viz                  # -> perdura_mindmap.html
    python perdura.py viz --out mind.html

Also draws `perdura_memoric.collision_candidates()` as dotted amber lines —
unlinked, lexically-close claims from different workers/streams that no
edge connects yet. This is the cross-stream contradiction E3 ingestion
adapters create exposure to (docs/enterprise.md §1): an incident's claimed
root cause and an ADR's context claim, never linked, surfaced visually
instead of waiting on an operator to run `--audit-every`. Same O(n^2)
unindexed scan as the CLI audit — fine for a demo-sized graph, the known
scalability gap for a large one (see collision_candidates' docstring).
"""

import json
from string import Template

from perdura_memoric import collision_candidates

_TEMPLATE = Template("""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Perdura — Mind Map</title>
<style>
  html,body{margin:0;height:100%;background:#0a0e1f;font-family:ui-monospace,monospace;overflow:hidden}
  canvas{display:block;cursor:grab}
  #hud{position:fixed;top:14px;left:16px;color:#97a3c4;font-size:12px;pointer-events:none}
  #hud b{color:#2dd9ff}
  #tip{position:fixed;display:none;max-width:340px;background:#0e1430;border:1px solid #2dd9ff55;
       border-radius:8px;padding:10px 12px;color:#f0f3fa;font-size:12px;line-height:1.5;pointer-events:none}
  #tip .meta{color:#5b6688;margin-top:4px}
  #legend{position:fixed;bottom:14px;left:16px;color:#5b6688;font-size:11px;pointer-events:none}
  #legend span{margin-right:14px}
  #legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px;vertical-align:-1px}
</style></head><body>
<canvas id="c"></canvas>
<div id="hud"><b>perdura</b> · $count_nodes nodes · $count_edges edges · $count_collisions collisions · contention $contention<br>drag to pan · wheel to zoom</div>
<div id="tip"></div>
<div id="legend">
  <span><i style="background:#2dd9ff"></i>question</span>
  <span><i style="background:#f0f3fa"></i>claim</span>
  <span><i style="background:#3ddc97"></i>evidence</span>
  <span><i style="background:#ffb454"></i>decision</span>
  <span><i style="background:#ff5d8f"></i>rejected</span>
  <span style="color:#ff5d8f">— contradicts</span>
  <span style="color:#ffb454">⋯ collision (unlinked, lexically close)</span>
</div>
<script>
const DATA = $data;
const COLORS = {question:"#2dd9ff", claim:"#f0f3fa", evidence:"#3ddc97",
                decision:"#ffb454", rejected:"#ff5d8f"};
const cv = document.getElementById("c"), ctx = cv.getContext("2d");
const tip = document.getElementById("tip");
let W, H; function resize(){W=cv.width=innerWidth;H=cv.height=innerHeight;}
resize(); addEventListener("resize", resize);

const nodes = DATA.nodes.map((n,i)=>({...n,
  x: W/2 + Math.cos(i*2.399)* (120+8*i), y: H/2 + Math.sin(i*2.399)*(80+5*i),
  vx:0, vy:0, r: n.type==="question"?9: n.type==="decision"?8:6}));
const byId = Object.fromEntries(nodes.map(n=>[n.id,n]));
const edges = DATA.edges.filter(e=>byId[e.src]&&byId[e.dst]);
const collisions = DATA.collisions.filter(c=>byId[c.src]&&byId[c.dst]);

let cam = {x:0,y:0,z:1}, dragging=null, hover=null;

function step(){
  for (const n of nodes){ n.fx=0; n.fy=0; }
  for (let i=0;i<nodes.length;i++) for (let j=i+1;j<nodes.length;j++){
    const a=nodes[i], b=nodes[j];
    let dx=a.x-b.x, dy=a.y-b.y, d2=dx*dx+dy*dy+0.01, d=Math.sqrt(d2);
    const f = 2600/d2;                       // repulsion
    a.fx+=dx/d*f; a.fy+=dy/d*f; b.fx-=dx/d*f; b.fy-=dy/d*f;
  }
  for (const e of edges){
    const a=byId[e.src], b=byId[e.dst];
    let dx=b.x-a.x, dy=b.y-a.y, d=Math.sqrt(dx*dx+dy*dy)+0.01;
    const f = 0.012*(d-110);                 // spring toward rest length
    a.fx+=dx/d*f*d*0.1; a.fy+=dy/d*f*d*0.1; b.fx-=dx/d*f*d*0.1; b.fy-=dy/d*f*d*0.1;
  }
  for (const n of nodes){
    n.fx += (W/2-n.x)*0.002; n.fy += (H/2-n.y)*0.002;   // gravity
    n.vx=(n.vx+n.fx)*0.82; n.vy=(n.vy+n.fy)*0.82;
    n.x+=Math.max(-12,Math.min(12,n.vx)); n.y+=Math.max(-12,Math.min(12,n.vy));
  }
}

function draw(){
  ctx.setTransform(1,0,0,1,0,0); ctx.clearRect(0,0,W,H);
  ctx.setTransform(cam.z,0,0,cam.z,cam.x,cam.y);
  for (const e of edges){
    const a=byId[e.src], b=byId[e.dst];
    ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y);
    if (e.type==="contradicts"){ ctx.strokeStyle="#ff5d8f"; ctx.lineWidth=1.6/cam.z; ctx.setLineDash([5,4]); }
    else { ctx.strokeStyle="rgba(151,163,196,0.28)"; ctx.lineWidth=1/cam.z; ctx.setLineDash([]); }
    ctx.stroke(); ctx.setLineDash([]);
  }
  for (const c of collisions){
    const a=byId[c.src], b=byId[c.dst];
    ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y);
    ctx.strokeStyle="#ffb454"; ctx.lineWidth=1.4/cam.z; ctx.setLineDash([2,5]);
    ctx.stroke(); ctx.setLineDash([]);
  }
  for (const n of nodes){
    ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,7);
    ctx.globalAlpha = n.superseded ? 0.28 : 1;
    ctx.fillStyle = COLORS[n.type] || "#97a3c4";
    if (n.type==="question"){ ctx.fillStyle="#0a0e1f"; ctx.fill();
      ctx.strokeStyle="#2dd9ff"; ctx.lineWidth=2.4/cam.z; ctx.stroke(); }
    else ctx.fill();
    if (n===hover){ ctx.strokeStyle="#fff"; ctx.lineWidth=1.5/cam.z; ctx.stroke(); }
    ctx.globalAlpha = 1;
  }
}
(function loop(){ step(); draw(); requestAnimationFrame(loop); })();

function toWorld(px,py){ return {x:(px-cam.x)/cam.z, y:(py-cam.y)/cam.z}; }
cv.addEventListener("mousedown", e=>{ dragging={x:e.clientX,y:e.clientY}; cv.style.cursor="grabbing"; });
addEventListener("mouseup", ()=>{ dragging=null; cv.style.cursor="grab"; });
addEventListener("mousemove", e=>{
  if (dragging){ cam.x+=e.clientX-dragging.x; cam.y+=e.clientY-dragging.y;
                 dragging={x:e.clientX,y:e.clientY}; return; }
  const w=toWorld(e.clientX,e.clientY);
  hover = nodes.find(n=>{const dx=n.x-w.x,dy=n.y-w.y;return dx*dx+dy*dy<(n.r+4)*(n.r+4);})||null;
  if (hover){
    tip.style.display="block"; tip.style.left=(e.clientX+14)+"px"; tip.style.top=(e.clientY+14)+"px";
    tip.innerHTML = "<b style='color:"+(COLORS[hover.type]||"#97a3c4")+"'>"+hover.type+"</b> "+
      (hover.superseded?"<i style='color:#5b6688'>(superseded)</i> ":"")+
      esc(hover.text)+"<div class='meta'>conf "+hover.confidence.toFixed(2)+
      (hover.tags.length? " · "+esc(hover.tags.join(", ")):"")+"</div>";
  } else tip.style.display="none";
});
cv.addEventListener("wheel", e=>{ e.preventDefault();
  const k = e.deltaY<0?1.12:0.89, w=toWorld(e.clientX,e.clientY);
  cam.z=Math.max(0.25,Math.min(4,cam.z*k));
  cam.x=e.clientX-w.x*cam.z; cam.y=e.clientY-w.y*cam.z; }, {passive:false});
function esc(s){const d=document.createElement("i");d.textContent=s;return d.innerHTML;}
</script></body></html>
""")


def render(graph) -> str:
    """Render a Graph into a standalone HTML mind map."""
    collisions = collision_candidates(graph)
    data = {
        "nodes": [{"id": n.id, "type": n.type, "text": n.text,
                   "confidence": n.confidence, "tags": n.domain_tags or [],
                   "superseded": n.superseded_by is not None}
                  for n in graph.nodes.values()],
        "edges": [{"src": e.src, "dst": e.dst, "type": e.type}
                  for e in graph.edges.values()],
        "collisions": [{"src": a.id, "dst": b.id} for a, b in collisions],
    }
    return _TEMPLATE.substitute(
        data=json.dumps(data),
        count_nodes=len(data["nodes"]),
        count_edges=len(data["edges"]),
        count_collisions=len(data["collisions"]),
        contention=graph.contention())


def write(graph, out: str = "perdura_mindmap.html") -> str:
    with open(out, "w", encoding="utf-8") as f:
        f.write(render(graph))
    return out
