const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../apps_script/recalc_button.gs'), 'utf8');
let count = 0;
function fixture(replies, properties={RECALC_URL:'https://example.test/api/recalc', RECALC_TOKEN:'test-secret'}) {
  const alerts=[], toasts=[], calls=[];
  let now=0;
  class Clock extends Date { static now() { return now; } }
  const ss={toast:(...args)=>toasts.push(args),getSpreadsheetTimeZone:()=> 'Etc/GMT-7'};
  const ctx = vm.createContext({Date:Clock,SpreadsheetApp:{getActive:()=>ss,getUi:()=>({alert:m=>alerts.push(m)})},
    PropertiesService:{getScriptProperties:()=>({getProperty:k=>properties[k]})},
    Utilities:{sleep:n=>{now+=n;},formatDate:d=>d.toISOString()},
    UrlFetchApp:{fetch:(url,options)=>{
      calls.push({url,options});
      const reply=replies.shift(); if (!reply) throw new Error('no response');
      if (reply instanceof Error) throw reply;
      return {getResponseCode:()=>reply.code ?? 200, getContentText:()=> typeof reply.body==='string'?reply.body:JSON.stringify(reply.body)};
    }}});
  vm.runInContext(source,ctx);
  return {ctx,alerts,toasts,calls,run:name=>vm.runInContext(name+'()',ctx)};
}
const state=(status,extra={})=>({running:status==='running',needs_review:false,last:{status,error:status==='error'?'Operations, row 3: choose a wallet.':null,started_at:'2026-09-27T20:41:25.594911+00:00',finished_at:'2026-09-27T20:41:56.498806+00:00',output:'run1'},...extra});
function test(name,fn) {fn();count++;}
test('success after polling',()=>{
 const f=fixture([{body:state('running')},{body:state('ok')}]);f.run('recalc');
 assert.equal(f.alerts.length,0);assert.match(f.toasts.at(-1)[0],/Done/);
 assert.deepEqual(f.calls.map(c=>c.options.method),['post','get']);
 assert.equal(f.calls[0].options.followRedirects,false);assert.equal(f.calls[0].options.timeoutSeconds,20);
});
test('input failure shown in dialog',()=>{
 const f=fixture([{body:state('running')},{body:state('error')}]);f.run('recalc');
 assert.match(f.alerts[0],/row 3/);assert.ok(f.toasts.every(t=>t[2]>0));
});
test('needs review outranks old success',()=>{
 const f=fixture([{body:state('ok',{needs_review:true})}]);f.run('showStatus');
 assert.match(f.alerts[0],/needs review/);assert.ok(!f.toasts.some(t=>t[0].startsWith('Done')));
});
test('needs review with empty journal',()=>{
 const f=fixture([{body:{running:false,last:null,needs_review:true}}]);f.run('showStatus');assert.match(f.alerts[0],/needs review/);
});
for (const [code,text] of [[401,/TOKEN/],[403,/denied access/],[404,/not found/],[429,/minute/],[302,/redirects/],[502,/HTTP 502/]]) {
 test('HTTP '+code,()=>{const f=fixture([{code,body:'<html>upstream failed</html>'}]);f.run('recalc');assert.match(f.alerts[0],text);assert.ok(!f.alerts[0].includes('<html>'));});
}
test('409 detail shown',()=>{const f=fixture([{code:409,body:{detail:'Check the sheet before retrying.'}}]);f.run('recalc');assert.match(f.alerts[0],/Check the sheet/);});
test('JSON 503 message shown',()=>{const f=fixture([{code:503,body:{detail:'No free disk space.'}}]);f.run('showStatus');assert.match(f.alerts[0],/disk space/);});
test('POST timeout not retried',()=>{const f=fixture([new Error('secret private server trace')]);f.run('recalc');assert.equal(f.calls.length,1);assert.match(f.alerts[0],/may have started/);assert.ok(!f.alerts[0].includes('secret'));});
test('GET timeout preserves uncertainty',()=>{const f=fixture([{body:state('running')},new Error('network')]);f.run('recalc');assert.match(f.alerts[0],/may have continued/);assert.equal(f.calls.length,2);});
test('malformed JSON',()=>{const f=fixture([{body:'<html>login</html>'}]);f.run('showStatus');assert.match(f.alerts[0],/unexpected response/);});
test('malformed state',()=>{const f=fixture([{body:{running:false,last:[]}}]);f.run('showStatus');assert.match(f.alerts[0],/unexpected/);});
test('missing properties',()=>{const f=fixture([],{});f.run('recalc');assert.equal(f.calls.length,0);assert.match(f.alerts[0],/Script properties/);});
test('invalid URL',()=>{const f=fixture([],{RECALC_URL:'http://example.test/api/recalc',RECALC_TOKEN:'token'});f.run('recalc');assert.equal(f.calls.length,0);assert.match(f.alerts[0],/HTTPS/);});
test('missing or bad timestamp never masks error',()=>{
 const bad=state('error');bad.last.finished_at='bad';const f=fixture([{body:bad}]);f.run('showStatus');assert.match(f.alerts[0],/row 3/);assert.match(f.alerts[0],/time not specified/);
});
test('different run cannot be mistaken for requested success',()=>{
 const next=state('ok');next.last.output='run2';const f=fixture([{body:state('running')},{body:next}]);f.run('recalc');assert.match(f.alerts[0],/another run/);
});
test('wait deadline leaves server running, no duplicate POST',()=>{
 const f=fixture(Array.from({length:40},()=>({body:state('running')})));f.run('recalc');assert.match(f.toasts.at(-1)[0],/still running/);assert.equal(f.calls.filter(c=>c.options.method==='post').length,1);
});
test('deployment busy shown',()=>{const f=fixture([{body:state('ok',{busy:true,detail:'The server is updating.'})}]);f.run('showStatus');assert.match(f.alerts[0],/updating/);});
console.log(`${count} Apps Script error/status checks passed; no external requests.`);
