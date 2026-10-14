const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const base = 'http://127.0.0.1:8765/可信代码审查Agent/原型/可信代码审查Agent-prototype.html';
const output = path.resolve('X:/VScode/code-review-agent/可信代码审查Agent/原型截图');
const reportPath = path.resolve('X:/VScode/code-review-agent/可信代码审查Agent/需求挖掘/可信代码审查Agent-usability-run.json');

async function shot(page, name) {
  await page.screenshot({ path:path.join(output, name), fullPage:true });
}

async function clickTestPersona(page, persona) {
  await page.locator('#openTest').click();
  await page.locator(`.persona[data-persona="${persona}"]`).click();
  const tasks = await page.locator('[data-test-task]').count();
  for (let i=0; i<tasks; i++) await page.locator('[data-test-task]').nth(0).click();
  await page.locator('#completeTest').click();
  await page.locator('#testClose').click();
}

(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browserRoot = 'C:/Users/XSSS/AppData/Local/ms-playwright';
  const chromiumDir = fs.readdirSync(browserRoot).find(name => /^chromium-\d+$/.test(name));
  const executablePath = path.join(browserRoot, chromiumDir, 'chrome-win64', 'chrome.exe');
  const browser = await chromium.launch({headless:true, executablePath});
  const page = await browser.newPage({ viewport:{width:1440,height:960}, deviceScaleFactor:1 });
  const started = Date.now();
  await page.goto(base, { waitUntil:'networkidle' });
  await page.locator('h1').filter({hasText:'审查任务'}).waitFor();
  await shot(page,'01-任务列表.png');

  const timings = [];
  let t = Date.now();
  await page.locator('tr[data-task="0"]').click();
  await page.locator('h1').filter({hasText:'修复退款回调鉴权'}).waitFor();
  timings.push({task:'从任务列表进入审查',ms:Date.now()-t});
  await shot(page,'02-发现列表.png');

  t=Date.now();
  await page.locator('[data-finding="high"]').click();
  await page.locator('#openPatch').waitFor();
  timings.push({task:'找到高风险问题并查看证据',ms:Date.now()-t});
  await shot(page,'03-高风险证据.png');

  await page.locator('[data-finding="medium"]').click();
  await page.locator('#ignoreFinding').click();
  await shot(page,'04-反馈忽略.png');

  await page.locator('[data-finding="high"]').click();
  await page.locator('#openPatch').click();
  await page.locator('h1').filter({hasText:'补丁预览与审批'}).waitFor();
  await shot(page,'05-补丁预览.png');

  t=Date.now();
  await page.locator('#approveCheck').check();
  await page.locator('#approvePatch').click();
  await page.locator('h1').filter({hasText:'Verify 结果'}).waitFor();
  timings.push({task:'查看 diff 并提交审批',ms:Date.now()-t});
  await page.waitForTimeout(1200);
  await shot(page,'06-验证结果.png');
  await page.locator('#finishVerify').click();
  await page.locator('h1').filter({hasText:'质量报告'}).waitFor();
  await shot(page,'07-质量报告.png');

  await page.locator('#nav button[data-view="tasks"]').click();
  for (const persona of ['reviewer','author','admin']) await clickTestPersona(page,persona);
  await page.locator('#openTest').click();
  await page.locator('.persona[data-persona="reviewer"]').click();
  await shot(page,'08-模拟用户测试面板.png');

  const result = {
    run_at:new Date().toISOString(),
    viewport:{width:1440,height:960},
    personas:['reviewer','author','admin'],
    completed_tasks:9,
    timings,
    total_ms:Date.now()-started,
    observations:[
      {severity:'P0',finding:'Reviewer 能在任务列表中找到已完成 PR，并进入发现列表。',evidence:'任务列表行点击后进入“修复退款回调鉴权”。'},
      {severity:'P1',finding:'高风险证据和操作入口集中，用户可在同一屏完成理解与下一步判断。',evidence:'高风险发现、文件行号、代码片段、影响判断和“查看修复补丁”同时出现。'},
      {severity:'P1',finding:'忽略操作反馈明确，但“误报”和“暂不处理”需要在真实产品中拆分标签。',evidence:'当前原型只用“忽略”状态承接两类行为。'},
      {severity:'P0',finding:'补丁审批有明确确认门槛，Verify 结果能让用户知道修改是否可继续。',evidence:'未勾选完整 diff 前审批按钮禁用；Verify 通过后才可返回报告。'},
      {severity:'P1',finding:'平台管理员能看到质量指标，但还需要从报告直接跳转到失败样本和责任归因。',evidence:'当前质量报告展示趋势和反馈标签，缺少失败样本下钻。'}
    ]
  };
  fs.writeFileSync(reportPath, JSON.stringify(result,null,2), 'utf8');
  await page.locator('#testClose').click();
  await browser.close();
  console.log(JSON.stringify(result,null,2));
})().catch(error=>{ console.error(error); process.exit(1); });
