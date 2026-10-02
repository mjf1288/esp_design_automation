import { chromium } from 'playwright'
import fs from 'node:fs'

const out='/home/user/workspace/esp/frontend/qa'
const base='http://127.0.0.1:4173'
const designId='839ec531-6d2b-4767-90fa-93ceb499c100'
fs.mkdirSync(out,{recursive:true})

async function useSavedDesign(page){
  await page.route('**/api/cases/demo-permian-h12/design',async route=>{
    if(route.request().method()!=='POST') return route.continue()
    const response=await page.request.get(`http://127.0.0.1:8000/api/designs/${designId}`)
    await route.fulfill({status:response.status(),contentType:'application/json',body:await response.text()})
  })
}
async function useScratchTenantSurvival(page){
  await page.route('**/api/empirical/survival**',async route=>{
    const response=await page.request.get('http://127.0.0.1:8000/api/empirical/survival?pump_model=RC2500&confidence_level=0.95',{headers:{'X-Tenant-Id':'qa-survival'}})
    await route.fulfill({status:response.status(),contentType:'application/json',body:await response.text()})
  })
}
async function capture(page,label){
  await page.goto(base,{waitUntil:'domcontentloaded'})
  await page.waitForSelector('[data-testid="view-results"]',{timeout:20000})
  await page.getByTestId('nav-empirical').click()
  await page.waitForSelector('.survival-chart canvas',{timeout:20000})
  await page.screenshot({path:`${out}/empirical-populated-${label}-dark.png`,fullPage:true})
  const response=await page.request.get('http://127.0.0.1:8000/api/empirical/survival?pump_model=RC2500&confidence_level=0.95',{headers:{'X-Tenant-Id':'qa-survival'}})
  const body=await response.json()
  let prior=1
  for(const point of body.estimate.points){
    if(point.n_events===0 && point.survival_probability!==prior) throw new Error(`Censor-only point at ${point.time_days} days changed survival`)
    if(point.n_events>0 && point.survival_probability>=prior) throw new Error(`Failure point at ${point.time_days} days did not drop survival`)
    prior=point.survival_probability
  }
  if(!await page.locator('.survival-chart canvas').count()) throw new Error('Live survival chart did not render')
  console.log(label,await page.locator('.survival-summary').innerText(),'validated failure-only drops')
}

const browser=await chromium.launch({headless:true})
const desktop=await browser.newPage({viewport:{width:1280,height:900}})
await useSavedDesign(desktop)
await useScratchTenantSurvival(desktop)
await capture(desktop,'1280')
const mobile=await browser.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true})
const phone=await mobile.newPage()
await useSavedDesign(phone)
await useScratchTenantSurvival(phone)
await capture(phone,'375')
await mobile.close()
await browser.close()
