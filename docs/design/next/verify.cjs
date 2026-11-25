// Run from a checkout with Playwright, Chromium and axe-core available.
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const {chromium} = require('playwright');
const axe = process.env.AXE_CORE_PATH || require.resolve('axe-core/axe.min.js');
const base = pathToFileURL(path.join(__dirname,'workspace.html')).href;
const gallery = pathToFileURL(path.join(__dirname,'../index.html')).href;
const core=['catalog','journal','people','teachers','profile','forms','settings','preview'];
const future=['today','load','classes','stats','widgets'];
async function run(){
 const browser=await chromium.launch(),results=[];
 const widths=process.env.REVIEW_WIDTHS?process.env.REVIEW_WIDTHS.split(',').map(Number):[390,768,1366,1440,1920];
 try{
 for (const width of widths) {
  const page=await browser.newPage({viewport:{width,height:900},deviceScaleFactor:1});
  const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  page.on('requestfailed',r=>errors.push(r.url().split('/').pop()+': '+r.failure().errorText));
  const entries=[...core.flatMap(k=>['S','C','D'].map(v=>[k,v])),...future.map(k=>[k,'C']),['gallery','C']];
  for(const [screen,variant] of entries){
   errors.length=0;
   await page.goto(screen==='gallery'?gallery:base+'?v='+variant+'&screen='+screen);
   await page.evaluate(()=>document.fonts.ready);
   const metrics=await page.evaluate(()=>{
    const root=document.querySelector('main'),b=root.getBoundingClientRect();
    const buttons=[...root.querySelectorAll('a[href],button,input:not([type=hidden]),select,summary')].filter(e=>{const r=e.getBoundingClientRect();return r.width&&r.height&&r.y>=0&&r.y<innerHeight&&r.left<innerWidth&&r.right>0});
    return {heading:root.querySelector('h1,h2')?.innerText??'',characters:root.innerText.length,pageOverflow:document.documentElement.scrollWidth-innerWidth,visibleActions:buttons.length,availableWidth:Math.round(b.width)};
   });
   const row={width,screen,variant,...metrics,errors:[...errors],violations:[]};
   {
    await page.addScriptTag({path:axe});
    const report=await page.evaluate(()=>axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21a','wcag21aa']}}));
    row.violations=report.violations.map(v=>({id:v.id,impact:v.impact,nodes:v.nodes.map(n=>n.target.join(' ')).slice(0,16),count:v.nodes.length}));
   }
   if(width===1440&&screen!=='gallery'&&process.env.REVIEW_CAPTURE==='1'){
    await page.evaluate(()=>document.body.classList.add('capturing'));
    await page.screenshot({path:path.join(__dirname,'shots',variant+'-'+screen+'.png'),animations:'disabled',fullPage:true});
   }
   results.push(row);
  }
  await page.close();
 }
 }finally{await browser.close()}
 results.sort((a,b)=>a.width-b.width||a.screen.localeCompare(b.screen)||a.variant.localeCompare(b.variant));
 const out=process.env.REVIEW_RESULTS||path.join(__dirname,'verification.json');
 fs.writeFileSync(out,JSON.stringify(results,null,1)+'\n');
 const failed=results.filter(r=>r.pageOverflow>1||r.errors.length||r.characters<80||r.violations.length);
 console.log(JSON.stringify({checks:results.length,failed:failed.map(r=>[r.width,r.screen,r.variant,r.pageOverflow,r.errors,r.violations.map(v=>v.id)]),out}));
 if(failed.length)process.exitCode=1;
}
run().catch(e=>{console.error(e);process.exitCode=1});
