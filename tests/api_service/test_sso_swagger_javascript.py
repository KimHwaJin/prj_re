"""Execute the served Swagger scripts to verify request headers and origin boundaries."""

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

import dtest.settings.loader as service_settings
from dtest.bootstrap import create_app


def test_swagger_scripts_send_csrf_only_to_own_api(tmp_path, monkeypatch):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Needs Node for executing Swagger JavaScript")
    monkeypatch.setattr(service_settings, "_snapshot", None)
    app = create_app(
        service_settings.load_settings(
            config={
                "MODEL_PROVIDER": "mock",
                "AGENT_WORKER_ENABLED": False,
                "TASK_RECONCILER_ENABLED": False,
            },
            environ={},
        )
    )
    with TestClient(app) as client:
        html = client.get("/docs").text
        demo = client.get("/demo").text
    scripts = re.findall(r"<script>\s*(.*?)\s*</script>", html, re.S)
    assert len(scripts) == 2
    data = tmp_path / "scripts.json"
    data.write_text(json.dumps(scripts))
    program = tmp_path / "swagger-test.cjs"
    program.write_text(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const scripts=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const elements=new Map(),calls=[];let options=null,authenticated=true;
const token='opaque-test-csrf';
function SwaggerUIBundle(o){options=o;return {};}
SwaggerUIBundle.presets={apis:{}};SwaggerUIBundle.SwaggerUIStandalonePreset={};
const context={URL,console,SwaggerUIBundle,
 window:{location:{origin:'https://api.test',href:'https://api.test/docs'}},
 document:{getElementById(id){if(!elements.has(id))elements.set(id,{textContent:'',addEventListener(){}});return elements.get(id);}},
 async fetch(url,opts){calls.push([url,opts]);return {ok:authenticated,status:authenticated?200:401,
   async json(){return {user_name:'Test',role:'user',csrf_token:token};}};}
};
vm.createContext(context);for(const source of scripts)vm.runInContext(source,context);
(async()=>{
 await new Promise(setImmediate);
 assert.equal(elements.get('sso-login').href,'/api/v1/auth/login/sso?target=docs');
 assert.equal(calls.length,1);assert.equal(calls[0][1].credentials,'same-origin');
 for(const method of ['POST','PUT','PATCH','DELETE']){
   const request=await options.requestInterceptor({url:'https://api.test/api/v1/projects',method,headers:{}});
   assert.equal(request.headers['X-CSRF-Token'],token);assert.equal(request.credentials,'same-origin');
 }
 for(const url of ['https://evil.test/api/v1/projects','https://api.test/platform/submit']){
   const request=await options.requestInterceptor({url,method:'POST',headers:{}});
   assert.equal(request.headers['X-CSRF-Token'],undefined);
 }
 const get=await options.requestInterceptor({url:'https://api.test/api/v1/projects',method:'GET',headers:{}});
 assert.equal(get.headers['X-CSRF-Token'],undefined);assert.equal(calls.length,1);
 authenticated=false;await context.refreshIdentity();
 await assert.rejects(()=>options.requestInterceptor({url:'https://api.test/api/v1/projects',method:'POST',headers:{}}));
 assert.equal(elements.get('sso-status').textContent,'로그인이 필요합니다');
 console.log('Swagger JS: cookie credentials, CSRF, off-origin guard, expiry passed');
})().catch(error=>{console.error(error);process.exit(1);});
""")
    result = subprocess.run(
        [node, str(program), str(data)],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    # Syntax-check the updated demo separately without making browser/API requests.
    demo_script = tmp_path / "demo.js"
    demo_script.write_text(
        "\n".join(
            re.findall(
                r'<script(?: id="console-app")?>\s*(.*?)\s*</script>',
                demo,
                re.S,
            )
        )
    )
    result = subprocess.run(
        [node, "--check", str(demo_script)],
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
