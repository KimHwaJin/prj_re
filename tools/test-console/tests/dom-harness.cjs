// Development-only DOM double: executes the shipped inline controller in Node.
// It checks DOM state and actual HTTP request contracts, not pixels or layout.
const fs=require('node:fs');const vm=require('node:vm');const {randomUUID}=require('node:crypto');
class Element {
 constructor(tag){this.tagName=tag.toUpperCase();this.children=[];this.dataset={};this.listeners={};this.className='';this.value='';this._text='';this.disabled=false;this.checked=false;this.style={};this.classList={add:(...n)=>{this.className=[...new Set([...this.className.split(' '),...n])].join(' ').trim();},remove:(...n)=>{this.className=this.className.split(' ').filter(v=>!n.includes(v)).join(' ');},contains:n=>this.className.split(' ').includes(n),toggle:(n,on)=>{const yes=on??!this.classList.contains(n);yes?this.classList.add(n):this.classList.remove(n);return yes;}};}
 get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}set textContent(v){this._text=String(v);this.children=[];}
 append(...nodes){for(const node of nodes){node.parentElement=this;this.children.push(node);}}
 replaceChildren(...nodes){this._text='';this.children=[];this.append(...nodes);}
 addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
 async fire(type){for(const fn of this.listeners[type]||[])await fn({target:this});if(this['on'+type])await this['on'+type]({target:this});}
 async click(){if(!this.disabled)await this.fire('click');}
 showModal(){this.open=true;}close(){this.open=false;}
 querySelectorAll(selector){return this.children.flatMap(c=>[...(matches(c,selector)?[c]:[]),...c.querySelectorAll(selector)]);}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
}
function matches(e,s){if(s.startsWith('[')){const k=s.slice(6,-1).replace(/-([a-z])/g,(_,v)=>v.toUpperCase());return k in e.dataset;}if(s.startsWith('.'))return e.classList.contains(s.slice(1));return e.tagName===s.toUpperCase();}
function harness(html,{base,fetch,storage=new Map(),runtime}={}){
 const ids=new Map();const roots=[];for(const m of html.matchAll(/<([a-z][a-z0-9]*)\b[^>]*\bid="([^"]+)"[^>]*>/g)){const e=new Element(m[1]);e.id=m[2];ids.set(e.id,e);roots.push(e);}
 for(const m of html.matchAll(/<button\b([^>]*)>([^<]*)<\/button>/g)){const id=/id="([^"]+)"/.exec(m[1]);const e=id?ids.get(id[1]):new Element('button');if(!id)roots.push(e);e.textContent=m[2];for(const a of m[1].matchAll(/data-([a-z-]+)="([^"]+)"/g))e.dataset[a[1].replace(/-([a-z])/g,(_,v)=>v.toUpperCase())]=a[2];}
 // Supply initial containment needed by the shipped controller.
 const logParent=new Element('div');logParent.append(ids.get('logFilters'));roots.push(logParent);
 ids.get('memoryConflict').append(new Element('pre'));
 ids.get('fixtures').textContent=/<script id="fixtures" type="application\/json">([\s\S]*?)<\/script>/.exec(html)[1];
 const document={getElementById:id=>ids.get(id),createElement:t=>new Element(t),createTextNode:text=>{const e=new Element('#text');e.textContent=text;return e;},querySelectorAll:s=>[...new Set(roots.flatMap(e=>[...(matches(e,s)?[e]:[]),...e.querySelectorAll(s)]))]};
 const timers=new Set();const location={protocol:base?'http:':'file:',origin:base?new URL(base).origin:'null',assign:url=>location.assigned=url};
 const window={location,addEventListener:()=>{}};
 if(base)window.TEST_CONSOLE_CONFIG=runtime||{apiBase:base,returnTo:'/test-console',fixture:true,executor:true};
 const context={document,window,location,crypto:{randomUUID},URL,Blob,TextDecoder,AbortController,sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},confirm:()=>true,fetch:fetch||(()=>{throw Error('Preview must never call fetch');}),console,setTimeout:(fn,ms)=>{const t=setTimeout(()=>{timers.delete(t);fn();},ms);t.unref();timers.add(t);return t;},clearTimeout:t=>{clearTimeout(t);timers.delete(t);}};
 const script=/<script id="console-app">([\s\S]*?)<\/script>/.exec(html)[1];vm.runInNewContext(script,context,{filename:'test-console-inline.js'});
 const all=s=>document.querySelectorAll(s);const find=(root,label)=>root.querySelectorAll('button').find(b=>b.textContent===label);
 return {ids,window,context,all,find,async field(control,value){control.value=value;await control.fire('input');},close(){for(const timer of timers)clearTimeout(timer);}};
}
module.exports={harness};
