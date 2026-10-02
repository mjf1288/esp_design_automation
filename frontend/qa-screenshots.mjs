import { chromium } from 'playwright'
import fs from 'node:fs'
const out='/home/user/workspace/esp/frontend/qa'
fs.mkdirSync(out,{recursive:true})
const browser=await chromium.launch({headless:true})
const page=await browser.newPage({viewport:{width:1280,height:900}})
await page.route('**/api/**',route=>route.abort())
await page.goto('http://127.0.0.1:4173',{waitUntil:'domcontentloaded'})
await page.waitForSelector('[data-testid="nav-results"]')
await page.waitForSelector('.fixture-badge')
for(const view of ['intake','case','results','timeline','empirical']){
  await page.getByTestId(`nav-${view}`).click()
  await page.screenshot({path:`${out}/${view}-1280-dark.png`,fullPage:true})
}
await page.getByTestId('button-theme').click()
await page.getByTestId('nav-results').click()
await page.screenshot({path:`${out}/results-1280-light.png`,fullPage:true})
const mobile=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true})
const p=await mobile.newPage()
await p.route('**/api/**',route=>route.abort())
await p.goto('http://127.0.0.1:4173',{waitUntil:'domcontentloaded'})
await p.waitForSelector('.fixture-badge')
for(const view of ['intake','case','results','timeline','empirical']){
  await p.getByTestId(`nav-${view}`).click()
  await p.screenshot({path:`${out}/${view}-375-dark.png`,fullPage:true})
}
await p.getByTestId('button-theme').click()
await p.getByTestId('nav-results').click()
await p.screenshot({path:`${out}/results-375-light.png`,fullPage:true})
console.log(await page.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,nav:document.querySelector('nav')?.getBoundingClientRect().toJSON()})))
console.log(await p.evaluate(()=>({width:innerWidth,height:innerHeight,scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,nav:document.querySelector('nav')?.getBoundingClientRect().toJSON()})))
await mobile.close();await browser.close()
