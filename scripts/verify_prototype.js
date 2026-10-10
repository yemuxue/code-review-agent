const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async()=>{
  const root = 'C:/Users/XSSS/AppData/Local/ms-playwright';
  const dir = fs.readdirSync(root).find(x => /^chromium-\d+$/.test(x));
  const browser = await chromium.launch({headless:true, executablePath:path.join(root,dir,'chrome-win64','chrome.exe')});
  const page = await browser.newPage({viewport:{width:1440,height:960}});
  const errors=[];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', msg => { if (msg.type()==='error') errors.push(msg.text()); });
  await page.goto('http://127.0.0.1:8765/可信代码审查Agent/原型/可信代码审查Agent-prototype.html',{waitUntil:'networkidle'});
  const homeTitle=await page.locator('h1').innerText();
  await page.locator('tr[data-task="0"]').click();
  await page.locator('#openPatch').click();
  const patchTitle=await page.locator('h1').innerText();
  await page.locator('#backReview').click();
  await page.locator('#nav button[data-view="tasks"]').click();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'X:/VScode/code-review-agent/可信代码审查Agent/原型截图/09-移动端任务列表.png',fullPage:true});
  console.log(JSON.stringify({homeTitle,patchTitle,consoleErrors:errors,mobileViewport:{width:390,height:844}},null,2));
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
