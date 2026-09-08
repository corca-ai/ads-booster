# ruff: noqa: E501 - JavaScript browser contract fixtures.
"""Run projections fence delayed observations and escape all human-report text."""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from ads_booster.marketing.agent_service.web_ui import AGENT_RUN_UI

if TYPE_CHECKING:
    from pathlib import Path


def test_delayed_report_cannot_replace_selected_run_and_failures_stay_local(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the browser-script contract harness")
    script = AGENT_RUN_UI.split("<script>", 1)[1].split("</script>", 1)[0]
    harness = r"""
const vm=require('node:vm'),assert=require('node:assert/strict');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,{value:'',innerHTML:'',textContent:'',hidden:false});return elements.get(id)};
const responses=new Map();
const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[]},location:{pathname:'/'},fetch:async()=>({json:async()=>({browser_login:false})}),console});
vm.runInContext(SOURCE,context);
context.responses=responses;vm.runInContext(`api=async path=>await new Promise((resolve,reject)=>responses.set(path,{resolve,reject}));`,context);
"""
    harness += r"""
const run=id=>({run:{run_id:id,state:'completed',revision:1,goal:{objective:id}},records:[]});
(async()=>{
const a=vm.runInContext("load('a')",context);responses.get('/v1/runs/a').resolve(run('a'));await a;
assert(responses.has('/v1/runs/a/performance'));
const b=vm.runInContext("load('b')",context);responses.get('/v1/runs/b').resolve(run('b'));await b;
responses.get('/v1/runs/b/performance').resolve({observations:[{observation_id:'b',account_id:'<img onerror=bad>',channel:'threads',country:'JP',publication_ref:'<script>x</script>',recorded_at:'2026-09-08T00:00:00.000001Z',window_start:'start',window_end:'end',views:0,likes:1,comments:2,clicks:null,installs:null},{account_id:'OLDER-ZERO',recorded_at:'2026-09-08T00:00:00Z'}]});
await new Promise(setImmediate);
assert(element('performance').innerHTML.includes('&lt;img'));
assert(!element('performance').innerHTML.includes('<img'));
assert(element('performance').innerHTML.includes('미보고'));
assert(element('performance').innerHTML.includes('조회 0'));
assert(element('performance').innerHTML.indexOf('&lt;img')<element('performance').innerHTML.indexOf('OLDER-ZERO'));
const snapshot=element('performance').innerHTML;
responses.get('/v1/runs/a/performance').resolve({observations:[{account_id:'OLD-PRIVATE'}]});await new Promise(setImmediate);
assert.equal(element('performance').innerHTML,snapshot);
responses.get('/v1/runs/b/assets').resolve({assets:[{asset_id:'image-b',kind:'native_trace_capture',origin:'human_reported',locale:'ja'}]});await new Promise(setImmediate);
assert(element('assets').innerHTML.includes('image-b'));
assert(element('assets').innerHTML.includes('사람 보고'));
const image=vm.runInContext("previewAsset('b',2,'image-b')",context);
responses.get('/v1/runs/b/assets/image-b').resolve({asset:{asset_id:'image-b',kind:'native_trace_capture',origin:'human_reported',source:'<img onerror=bad>',qa:['<script>'],revision:1},image_base64:'iVBORw0KGgoAAAAA',stale:true});await image;
assert(element('assetPreview').innerHTML.includes('data:image/png;base64,'));
assert(element('assetPreview').innerHTML.includes('&lt;img'));
assert(element('assetPreview').innerHTML.includes('재검토 필요'));
assert(element('assetPreview').innerHTML.includes('사람 보고'));
assert(element('assetPreview').innerHTML.includes('시각 검증 미수행'));
const oldImage=vm.runInContext("previewAsset('b',2,'old-image')",context);
const c=vm.runInContext("load('c')",context);responses.get('/v1/runs/c').resolve(run('c'));await c;
responses.get('/v1/runs/c/performance').reject(Error('private backend error'));await new Promise(setImmediate);
assert(element('performance').textContent.includes('불러오지 못'));
assert(!element('performance').textContent.includes('private backend'));
assert(element('detail').innerHTML.includes('<h3>c</h3>'));
const imageSnapshot=element('assetPreview').innerHTML;
responses.get('/v1/runs/b/assets/old-image').resolve({asset:{source:'PRIVATE-OLD'},image_base64:'iVBORw0KGgoAAAAA'});await oldImage;
assert.equal(element('assetPreview').innerHTML,imageSnapshot);
})();
"""
    path = tmp_path / "browser-contract.cjs"
    _ = path.write_text("const SOURCE=" + json.dumps(script) + ";\n" + harness)
    result = subprocess.run(  # noqa: S603 - repository UI and fixed local test script.
        [node, str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
