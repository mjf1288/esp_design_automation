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
async function settle(page,view){
  await page.waitForSelector(`[data-testid="view-${view}"]`,{timeout:20000})
  await page.waitForFunction(()=>!document.querySelector('.loading-view'),null,{timeout:20000})
}
async function capture(page,label){
  await page.getByTestId('nav-results').click()
  await page.waitForSelector('.chart-qualifications',{timeout:20000})
  await page.screenshot({path:`${out}/results-live-${label}-dark.png`,fullPage:true})
  await page.getByTestId('nav-empirical').click()
  await page.waitForSelector('[data-testid="view-empirical"] .empty-state',{timeout:20000})
  await page.screenshot({path:`${out}/empirical-live-${label}-dark.png`,fullPage:true})
}

const b=await chromium.launch({headless:true})
const p=await b.newPage({viewport:{width:1280,height:900}})
await useSavedDesign(p)
await p.goto(base,{waitUntil:'domcontentloaded'})
await settle(p,'results')
await p.waitForSelector('.chart-qualifications',{timeout:20000})
await p.waitForSelector('[data-testid="candidate-tie-note"]',{timeout:20000})
const desktopRanks=await p.locator('.candidate-rank').allInnerTexts()
if(!desktopRanks.length||desktopRanks.some(rank=>rank.trim()!=='#1 (tied)')) throw new Error(`Expected tied presentation, got ${desktopRanks.join(', ')}`)
const desktopScores=await p.locator('.candidate .score').allInnerTexts()
if(desktopScores.some(score=>!/^\d+\.\d{3}$/.test(score.trim()))) throw new Error(`Expected three-decimal score display, got ${desktopScores.join(', ')}`)
const envelope=await p.locator('.boundary-number + p + p').first().innerText()
if(envelope.includes('—–')||envelope.includes('–—')) throw new Error(`Invalid envelope dash rendering: ${envelope}`)
await p.locator('.candidate').nth(1).click()
await p.waitForSelector('.chart-qualifications',{timeout:20000})
await p.locator('.candidate').nth(0).click()
await p.waitForSelector('.chart-qualifications',{timeout:20000})
await capture(p,'1280')
console.log('desktop',await p.evaluate(()=>({scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,fixture:Boolean(document.querySelector('.fixture-badge'))})))

const mobile=await b.newContext({viewport:{width:375,height:812},isMobile:true,hasTouch:true})
const m=await mobile.newPage()
await useSavedDesign(m)
await m.goto(base,{waitUntil:'domcontentloaded'})
await settle(m,'results')
await capture(m,'375')
console.log('mobile',await m.evaluate(()=>({scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,fixture:Boolean(document.querySelector('.fixture-badge'))})))
await mobile.close()
await b.close()
