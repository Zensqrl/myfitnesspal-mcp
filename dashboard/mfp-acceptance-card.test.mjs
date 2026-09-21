import {test} from 'node:test';
import assert from 'node:assert/strict';
import {unpack, summarize, localDay, MfpAcceptanceCard} from './mfp-acceptance-card.mjs';
const now = Date.parse('2026-09-20T15:00:00Z');
test('MCP requests explicitly accept JSON using the existing HA session',async()=>{
  const context={config:{api_id:'mcp-test'},_hass:{callApi:async(method,path,body,headers)=>{
    assert.equal(method,'POST'); assert.equal(path,'mcp/mcp-test');
    assert.equal(headers.Accept,'application/json');
    assert.equal(body.method,'tools/list'); assert.equal(body.jsonrpc,'2.0');
    return {jsonrpc:'2.0',result:{tools:[]}};
  }}};
  assert.deepEqual(await MfpAcceptanceCard.prototype.rpc.call(context,'tools/list',{}),{tools:[]});
});
function fixture() {
  return {status:'cached',stale:false,retrieved_at:'2026-09-20T14:55:00Z',
    data:{day:'2026-09-20',nutrition:{nutrients:{calories:2000,protein:100,carbohydrates:200,fat:60},
      goals:{calories:1900,protein:120,carbohydrates:220,fat:70}}}};
}
test('HA nested MCP wrappers decode without persisting diary data',()=>{
  const data=fixture();
  assert.deepEqual(unpack({jsonrpc:'2.0',result:{content:[{type:'text',text:JSON.stringify({structuredContent:data})}]}}),data);
  assert.throws(()=>unpack({isError:true,content:[]}));
});
test('complete fresh contract includes units and signed remaining',()=>{
  const s=summarize(fixture(),'2026-09-20',now);
  assert.equal(s.passed,true); assert.equal(s.rows[0].remaining,-100);
  assert.deepEqual(s.rows.map(r=>r.unit),['kcal','g','g','g']);
});
test('missing targets, null, strings and missing cache never pass as zeros',()=>{
  const f=fixture(); f.data.nutrition.goals.protein=null;
  f.data.nutrition.nutrients.fat='60';
  const s=summarize(f,'2026-09-20',now);
  assert.equal(s.passed,false); assert.equal(s.rows[1].target,null);
  assert.equal(s.rows[3].total,null);
  assert.equal(summarize({status:'not_cached'},'2026-09-20',now).passed,false);
});
test('stale, future timestamp, unknown freshness and wrong date fail',()=>{
  for(const patch of [{stale:true},{stale:undefined},{retrieved_at:'2026-09-20T12:00:00Z'},
    {retrieved_at:'2026-09-21T15:00:00Z'},{retrieved_at:null}]) {
    assert.equal(summarize({...fixture(),...patch},'2026-09-20',now).passed,false);
  }
  assert.equal(summarize(fixture(),'2026-09-21',now).passed,false);
});
test('HA timezone determines diary date across UTC midnight',()=>{
  assert.equal(localDay('America/New_York',new Date('2026-09-21T01:00:00Z')),'2026-09-20');
});
