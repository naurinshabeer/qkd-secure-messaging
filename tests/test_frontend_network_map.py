import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        self.ids.update(value for name, value in attrs if name == "id" and value)


class NetworkMapGeometryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to execute the dashboard renderer")
    def test_six_node_map_edges_avoid_non_endpoint_nodes(self):
        html_path = ROOT / "web" / "index.html"
        app_path = ROOT / "web" / "app.js"
        html = html_path.read_text(encoding="utf-8")
        parser = IdParser()
        parser.feed(html)
        for element_id in ("network-diagram", "diagram-title", "diagram-description"):
            self.assertIn(element_id, parser.ids)

        state = {
            "topology": True,
            "links": [
                {"id": "A-R1", "a": "alice", "b": "r1"},
                {"id": "A-R2", "a": "alice", "b": "r2"},
                {"id": "A-R3", "a": "alice", "b": "r3"},
                {"id": "R1-R3", "a": "r1", "b": "r3"},
                {"id": "R1-R4", "a": "r1", "b": "r4"},
                {"id": "R2-R4", "a": "r2", "b": "r4"},
                {"id": "R3-R4", "a": "r3", "b": "r4"},
                {"id": "R4-B", "a": "r4", "b": "bob"},
            ],
            "paths": [["alice", "r1", "r4", "bob"], ["alice", "r2", "r4", "bob"],
                      ["alice", "r3", "r4", "bob"]],
            "last_path": ["alice", "r3", "r4", "bob"],
        }
        for link in state["links"]:
            link.update({"distance_km": 10, "noise": .01, "eve": 0, "up": True,
                         "pool_count": 2, "a_pool": 2, "b_pool": 2, "qber": .02})
        state["links"][0]["up"] = False

        script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const ids = new Map();
function element() {
  return {value:'', innerHTML:'', textContent:'', hidden:false, open:false, disabled:false,
    className:'', attributes:{}, addEventListener(){}, setAttribute(name, value){this.attributes[name]=value;}};
}
const document = {getElementById(id){if (!ids.has(id)) ids.set(id, element()); return ids.get(id);}, querySelectorAll(){return [];}};
const state = JSON.parse(process.argv[1]);
const context = {document, console, fetch:async()=>({ok:true,json:async()=>state})};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8') + '\nglobalThis.__renderMap = renderNetwork; globalThis.__stateMessage = stateMessage;', context);
const diamond = {topology:true, paths:[['alice','r1','bob'],['alice','r2','bob']], links:[
  ['A-R1','alice','r1'],['R1-B','r1','bob'],['A-R2','alice','r2'],['R2-B','r2','bob']
].map(([id,a,b])=>({id,a,b,distance_km:10,noise:.01,eve:0,up:true,pool_count:2,a_pool:2,b_pool:2,qber:.02}))};
context.__renderMap(diamond);
const defaultViewBox=document.getElementById('network-diagram').attributes.viewBox;
context.__renderMap(state);
const edgeMarkup = document.getElementById('diagram-links').innerHTML;
const nodeMarkup = document.getElementById('diagram-nodes').innerHTML;
const edges = [...edgeMarkup.matchAll(/<path class="diagram-edge" d="([^"]+)"/g)];
const nodes = [...nodeMarkup.matchAll(/<g class="diagram-node"><rect x="([^"]+)" y="([^"]+)" width="([^"]+)" height="([^"]+)"[^>]*><\/rect><text[^>]*>([^<]+)<\/text>/g)]
  .map(m => ({name:m[5].toLowerCase().replace(/^relay (\d+)$/, 'r$1'), left:+m[1], top:+m[2], width:+m[3], height:+m[4]}));
function intersects(a, b, rect) {
  let low=0, high=1, dx=b.x-a.x, dy=b.y-a.y;
  for (const [p,q] of [[-dx,a.x-rect.left],[dx,rect.left+rect.width-a.x],[-dy,a.y-rect.top],[dy,rect.top+rect.height-a.y]]) {
    if (!p) { if (q < 0) return false; continue; }
    const t=q/p;
    if (p<0) low=Math.max(low,t); else high=Math.min(high,t);
    if (low>high) return false;
  }
  return true;
}
const errors=[];
edges.forEach((match, index) => {
  const link=state.links[index];
  const points=[...match[1].matchAll(/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)/g)].map(m=>({x:+m[1],y:+m[2]}));
  nodes.filter(node => node.name!==link.a && node.name!==link.b).forEach(node => {
    const box={left:node.left,top:node.top,width:node.width,height:node.height};
    for (let i=0;i<points.length-1;i++) if (intersects(points[i],points[i+1],box)) errors.push(`${link.id} crosses ${node.name}`);
  });
});
console.log(JSON.stringify({errors, edgeCount:edges.length, nodeCount:nodes.length,
  viewBox:document.getElementById('network-diagram').attributes.viewBox,
  defaultViewBox,
  title:document.getElementById('diagram-title').textContent,
  header:document.getElementById('header-state').textContent,
  description:document.getElementById('diagram-description').textContent,
  status:context.__stateMessage(state), downStyled:edgeMarkup.includes('is-down'),
  pathHighlighted:edgeMarkup.includes('path-used')}));
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script, json.dumps(state), str(app_path)],
            check=True, capture_output=True, text=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["errors"], [], rendered)
        self.assertEqual(rendered["edgeCount"], 8)
        self.assertEqual(rendered["nodeCount"], 6)
        self.assertEqual(rendered["defaultViewBox"], "0 0 640 260")
        self.assertIn("6 nodes", rendered["header"])
        self.assertIn("8 links", rendered["header"])
        self.assertIn("3 routes", rendered["header"])
        self.assertIn("3 routes", rendered["description"])
        route_sentence = rendered["status"].split("Messages can use ", 1)[1]
        self.assertEqual(re.findall(r"Alice|Relay \d+|Bob", route_sentence),
                         ["Alice", "Relay 2", "Relay 4", "Bob"])
        self.assertTrue(rendered["downStyled"])
        self.assertTrue(rendered["pathHighlighted"])
        _, _, _, height = map(float, re.split(r"\s+", rendered["viewBox"]))
        self.assertGreater(height, 260)


if __name__ == "__main__":
    unittest.main()
