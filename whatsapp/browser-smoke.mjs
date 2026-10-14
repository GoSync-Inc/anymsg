// Offline sandbox check. Never opens WhatsApp or an account profile.
import puppeteer from 'puppeteer';
import wwjs from 'whatsapp-web.js';
if(typeof wwjs.Client!=='function')throw new Error('provider_import_failed');
let browser;
try {
  browser=await puppeteer.launch({headless:true,executablePath:'/usr/bin/chromium',
    userDataDir:'/tmp/pos-browser-smoke',args:['--disable-dev-shm-usage']});
  const page=await browser.newPage();await page.goto('data:text/html,<title>sandbox-smoke</title>');
  const title=await page.title();
  if(title!=='sandbox-smoke')throw new Error('wrong_title');
  await page.goto('chrome://sandbox');
  const status=await page.evaluate(()=>document.body.innerText);
  for(const required of ['PID namespaces\tYes','Network namespaces\tYes','Seccomp-BPF sandbox\tYes'])
    if(!status.includes(required))throw new Error('sandbox_incomplete');
  console.log(JSON.stringify({title,chromium:await browser.version(),sandbox:status}));
}finally{await browser?.close();}
