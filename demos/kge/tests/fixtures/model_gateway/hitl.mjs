import assert from 'node:assert/strict';
import { createApp } from '../../../portal/server/src/app.js';
import { openSqliteRepository } from '../../../portal/server/src/repository/sqlite.js';
import { memoryStore } from '../../../portal/server/src/conversations/index.js';
const repository=openSqliteRepository({PORTAL_SQLITE_PATH:':memory:'});
const {app}=createApp({repository,env:{KGE_URL:'http://127.0.0.1:'+process.env.ENDPOINT_PORT,PORTAL_SYNC_TOKEN:process.env.PORTAL_KEY,PORTAL_ASK_MODEL_PROVIDER:'vertex'},serviceAuth:{headers:async()=>({})},catalogueSync:false,uiPath:'not-present',tracing:null,conversationStore:memoryStore()});
const server=app.listen(0,'127.0.0.1'); await new Promise(r=>server.once('listening',r)); const base='http://127.0.0.1:'+server.address().port;
try {
 const thread=crypto.randomUUID(); const send=async(body)=>{ const r=await fetch(base+'/api/cerebro/stream',{method:'POST',headers:{'content-type':'application/json',cookie:'kge_session=alice'},body:JSON.stringify(body)}); return {status:r.status,text:await r.text()}; };
 const first=await send({input:{messages:[{type:'human',content:'Choose a book'}]},config:{configurable:{thread_id:thread}}});
 assert.equal(first.status,200); assert.match(first.text,/Which book/);
 const resume=await send({command:{resume:{pick:'1'}},config:{configurable:{thread_id:thread}}});
 assert.equal(resume.status,200); assert.match(resume.text,/Finished/); assert.match(resume.text,/"input_tokens":40/); assert.match(resume.text,/"calls":4/);
 console.log('Real LangGraph HITL resume → gateway → reserved MCP Ask → usage ledger: passed');
} finally {server.close();}
