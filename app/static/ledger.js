const ledgerEl=document.getElementById('ledger-archive');
const ledgerState={busy:false,record:ledgerEl?JSON.parse(ledgerEl.textContent):{}};
const notice=document.getElementById('ledger-notice');
const samples=window.LEDGER_SAMPLES||{};
function setBusy(on){ledgerState.busy=on;document.querySelectorAll('button').forEach(b=>b.disabled=on);}
async function call(url,body,file){
  setBusy(true);notice.textContent='正在处理台账…';
  try{
    let options={method:'POST'};
    if(file){const form=new FormData();form.append('file',file);const asOf=document.getElementById('as-of').value;if(asOf)form.append('as_of',asOf);options.body=form;}
    else{options.headers={'Content-Type':'application/json'};options.body=JSON.stringify(body);}
    const res=await fetch(url,options);const answer=await res.json();
    if(!res.ok)throw Error(answer.message||'请求失败');
    return answer;
  }finally{setBusy(false);}
}
function payload(){
  return{csv:document.getElementById('ledger-csv').value,as_of:document.getElementById('as-of').value||null};
}
async function publish(file){
  try{
    const answer=await call('/api/ledger/publish',payload(),file);
    notice.textContent=answer.reused?('幂等命中：复用 v'+answer.version+'，未新增版本'):('已发布台账 v'+answer.version);
    location.href='/work/8?version='+answer.version;
  }catch(e){notice.textContent=e.message;}
}
document.querySelectorAll('[data-sample]').forEach(b=>b.addEventListener('click',()=>{
  const text=samples[b.dataset.sample];if(text!==undefined)document.getElementById('ledger-csv').value=text;
}));
document.getElementById('seed-week1').addEventListener('click',()=>{document.getElementById('ledger-csv').value=samples.week1;publish();});
document.getElementById('seed-week2').addEventListener('click',()=>{document.getElementById('ledger-csv').value=samples.week2;publish();});
document.getElementById('seed-week3').addEventListener('click',()=>{document.getElementById('ledger-csv').value=samples.week3;publish();});
document.getElementById('ledger-publish').addEventListener('click',()=>publish());
document.getElementById('ledger-upload').addEventListener('click',()=>{
  const file=document.getElementById('ledger-file').files[0];
  if(!file){notice.textContent='先选择批次CSV文件。';return;}
  publish(file);
});
document.getElementById('ledger-preview').addEventListener('click',async()=>{
  try{
    const res=await fetch('/api/m8',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload())});
    const answer=await res.json();if(!res.ok)throw Error(answer.message||'试算失败');
    const counts=Object.fromEntries(answer.metrics.filter(m=>typeof m[1]==='number').map(m=>[m[0],m[1]]));
    document.getElementById('preview').innerHTML='<div class="preview-card"><b>试算结果（未落库）</b>'+
      '<p>在册 '+answer.entries.filter(e=>e.状态码==='ACTIVE').length+' · 缺失 '+answer.entries.filter(e=>e.状态码==='MISSING').length+
      ' · 撤销 '+answer.entries.filter(e=>e.状态码==='REVOKED').length+' · 过期 '+answer.entries.filter(e=>e.状态码==='EXPIRED').length+
      ' · 隔离 '+answer.quarantine.length+' · 守恒 '+(answer.conserved?'相符':'差异')+'</p><small>幂等键 '+answer.manifest.run_id+'</small></div>';
    notice.textContent='试算完成，确认无误后再发布。';
  }catch(e){notice.textContent=e.message;}
});
document.querySelectorAll('.rollback').forEach(b=>b.addEventListener('click',async()=>{
  if(!confirm('回滚到 v'+b.dataset.version+'？当前指针将切换，历史版本不删除。'))return;
  try{
    const res=await fetch('/api/ledger/rollback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:Number(b.dataset.version)})});
    const answer=await res.json();if(!res.ok)throw Error(answer.message||'回滚失败');
    location.href='/work/8?version='+answer.version;
  }catch(e){notice.textContent=e.message;}
}));
document.querySelectorAll('.ledger-review').forEach(b=>b.addEventListener('click',async()=>{
  if(!ledgerState.record.version){notice.textContent='没有可复核的版本。';return;}
  try{
    const res=await fetch('/api/ledger/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:ledgerState.record.version,revision:ledgerState.record.revision,decision:b.dataset.decision})});
    const answer=await res.json();if(!res.ok)throw Error(answer.message||'复核失败');
    location.reload();
  }catch(e){notice.textContent=e.message;}
}));
