// Pure browser helpers in isolation. No network or third-party dependencies.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const file = path.join(__dirname, '../dashboard/frontend/charts.js');
const context = vm.createContext({ Intl });
vm.runInContext(fs.readFileSync(file, 'utf8'), context);
const run = (expr) => vm.runInContext(expr, context);
const same = (a,b) => assert.equal(run(`colorSwatch(${JSON.stringify(a)})`),run(`colorSwatch(${JSON.stringify(b)})`),`${a} vs ${b}`);
for (const [a,b] of [['BLACK','black'],['Burgundy','maroon'],['Blue metallic','Blue'],['White pearl','White'],['Dark blue','dark-blue matte'],['darkblue','dark-blue'],['dark blue','Dark Blue'],['red and black','red-black'],['Red with black','red/black'],['light green','Light-green'],['#abc','#aabbcc']]) same(a,b);
assert.equal(run('paintColors("graphite").length'),1);
assert.equal(run('paintColors("black/chrome").length'),2);
assert.equal(run('paintColors("red, white, blue").length'),3);
for (const value of ['',null,125,'cosmic dust','<script>']) {
 assert.equal(run(`swatchTone(${JSON.stringify(value)})`),' is-unknown');
}
assert.equal(run('swatchTone("white")'),' is-light');
assert.equal(run('swatchTone("black")'),' is-dark');
assert.equal(run('statusColor("Sold")'),run('statusColor("Sold/Retired")'));
assert.equal(run('statusColor("New status")'),'var(--text-muted)');
assert.equal(run('fmtThb(null)'),'—');
assert.equal(run('fmtThb(-1500)'), '−1,500\u00a0฿');
assert.equal(run('fmtThb(1234567)'), '1,234,567\u00a0฿');
assert.equal(run('fmtDateShort("2026-09-22")'), 'Sep 22');
assert.equal(run('bucketLabel({start:"2026-09-01"},"month")'), 'Sep \u201926');
assert.equal(run('fmtPct(-0.01).text'),'0%');
// Extract pure spelling helper without starting the app.
const app = fs.readFileSync(path.join(__dirname,'../dashboard/frontend/app.js'),'utf8');
for (const name of ['pluralErrors']) {
 const fn=app.match(new RegExp(`function ${name}\\(n\\) \\{[\\s\\S]*?\\n\\}`));
 vm.runInContext(fn[0],context);
}
for (const [n,word] of [[1,'error'],[2,'errors'],[0,'errors'],[11,'errors'],[21,'errors']]) assert.equal(run(`pluralErrors(${n})`),word);
assert.equal(run('pluralBikes(1)'),'bike');
assert.equal(run('pluralInvestors(3)'),'investors');
console.log('Frontend helper checks passed: colors, shades, fallbacks, statuses, money, plurals.');
// Refresh must reject a failed POST instead of silently showing old data.
(async () => {
 const calls=[];
 const redirects=[];
 const ctx=vm.createContext({URL,Error,window:{location:{origin:'http://localhost'}},location:{pathname:'/',search:'',replace:u=>redirects.push(u)},fetch:async(url,opts)=>{calls.push(opts);return {status:502,ok:false,text:async()=>JSON.stringify({detail:'Could not read the sheet'})};}});
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../dashboard/frontend/api.js'),'utf8'),ctx);
 await assert.rejects(vm.runInContext('Api.refresh()',ctx),/Could not read the sheet/);
 assert.equal(calls[0].method,'POST');
 ctx.fetch=async()=>({status:401,ok:false});
 await assert.rejects(vm.runInContext('Api.refresh()',ctx),/Sign-in required/);
 assert.match(redirects[0],/^\/login/);
 console.log('Refresh errors and expired sessions handled correctly.');
})().catch(e=>{console.error(e);process.exitCode=1;});
