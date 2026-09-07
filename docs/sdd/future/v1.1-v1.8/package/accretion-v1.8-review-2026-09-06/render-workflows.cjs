const fs = require('fs');
const path = require('path');
const { instance } = require('/home/santapong/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@viz-js/viz');
const root=__dirname;
(async()=>{
 const viz=await instance();
 const receipts=[];
 for(const name of ['user-journey','physical-trial']){
  const source=fs.readFileSync(path.join(root,name+'.dot'),'utf8');
  const r=viz.renderFormats(source,['svg','json'],{engine:'dot'});
  if(r.status!=='success'||r.errors.length)throw Error(JSON.stringify(r.errors));
  let svg=r.output.svg;
  fs.writeFileSync(path.join(root,name+'.svg'),svg);
  fs.writeFileSync(path.join(root,name+'.geometry.json'),r.output.json);
  receipts.push({name,status:r.status,diagnostics:r.errors});
  const title=name==='user-journey'?'User journey':'Physical preparation and trial';
  const note=name==='user-journey'?'Human decisions are purple. Green boxes check evidence. Orange branches need extra gates. Red outcomes remain visible. Physical failures are excluded from the digital recovery loop.':'v1.8 supplies preparation advice only. The existing v0.6 gateway owns physical authority. A safe stop is not task success; a safety incident fails the gate even if the task completes. No automatic physical retry.';
  fs.writeFileSync(path.join(root,name+'.html'),`<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Accretion — ${title}</title><style>body{font:17px/1.5 system-ui,sans-serif;color:#172a3c;background:#f4f7fa;margin:0;padding:18px}main{max-width:1500px;margin:auto}nav{display:flex;gap:20px;flex-wrap:wrap;margin-bottom:16px}a{color:#215b91}figure{margin:0;background:white;border:1px solid #d6e1eb;border-radius:14px;padding:20px}svg{display:block;width:100%;height:auto;max-height:calc(100vh - 185px)}p{max-width:1100px;margin:14px auto 0}.status{font-weight:700} @media(max-width:800px){svg{max-height:none}body{padding:12px}figure{padding:8px}}</style><main><nav><a href="index.html">Review and capabilities</a><a href="architecture.html">System architecture</a><a href="${name==='user-journey'?'physical-trial':'user-journey'}.html">${name==='user-journey'?'Physical trial':'User journey'}</a><a download href="${name}.svg">Download SVG</a></nav><figure>${svg.replace(/<\?xml[^>]*>|<!DOCTYPE[\s\S]*?>/g,'')}</figure><p><span class="status">Forward design.</span> ${note}</p></main></html>`);
 }
 fs.writeFileSync(path.join(root,'workflow-render-receipts.json'),JSON.stringify(receipts,null,2)+'\n');
 console.log(receipts);
})();
