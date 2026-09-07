import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { resolve,dirname } from 'node:path';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const api=spawn(resolve(root,'.venv/bin/python'),['-m','uvicorn','backend.app:app','--host','127.0.0.1','--port','8000','--no-access-log','--no-proxy-headers'],{cwd:root,stdio:'inherit'});
const web=spawn('npm',['--prefix','frontend','run','dev'],{cwd:root,stdio:'inherit'});
const mail=spawn(resolve(root,'.venv/bin/python'),['-m','backend.mail_delivery'],{cwd:root,stdio:'inherit'});
const children=[api,web,mail];
let stopping=false;
function stop(code=0){if(stopping)return;stopping=true;for(const c of children)c.kill('SIGTERM');process.exitCode=code;}
for(const c of children){c.on('error',e=>{console.error(e.message);stop(1);});c.on('exit',code=>stop(code||0));}
process.on('SIGINT',()=>stop());process.on('SIGTERM',()=>stop());
