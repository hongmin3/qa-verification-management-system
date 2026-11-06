const assert = require('node:assert/strict');
const path = require('node:path');
const {buildSync}=require('esbuild');
const frontend=path.resolve(__dirname,'..');
const output=buildSync({stdin:{contents:`import React from 'react';import {renderToStaticMarkup} from 'react-dom/server';import {BodyMatch,BodyIndexPanel} from './src/components/BodySearch';import {runIndexBatches} from './src/bodyIndex';export {runIndexBatches};export function match(hit){return renderToStaticMarkup(<BodyMatch hit={hit}/>)};export function panel(admin){return renderToStaticMarkup(<BodyIndexPanel admin={admin} locked={false} runBatch={async()=>({processed:0,results:[],next_cursor:null,done:true,current_only:true,remaining:0})} onUpdated={()=>{}}/>)};`,resolveDir:frontend,loader:'tsx'},bundle:true,platform:'node',format:'cjs',write:false,jsx:'automatic'});
const compiled=new module.constructor();compiled._compile(output.outputFiles[0].text,path.join(frontend,'body-test.cjs'));
const {match,panel,runIndexBatches}=compiled.exports;
assert.match(match({body_status:'no_text',body_detail:null,snippet:null}),/OCR/);
assert.match(match({body_status:'failed',body_detail:'parser failed',snippet:null}),/parser failed/);
const html=match({body_status:'ready',snippet:'<img onerror=alert(1)>',version:'V3'});
assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img/);assert.match(html,/V3/);
assert.equal(panel(false),'');assert.match(panel(true),/본문 색인 갱신/);assert.match(panel(true),/Current/);
(async()=>{
 let stop=false,calls=0;const progress=[];
 await runIndexBatches(async()=>{calls++;return {processed:2,results:[],next_cursor:'next',done:false,current_only:true,remaining:4}},()=>stop,(result)=>{progress.push(result);stop=true},null);
 assert.equal(calls,1);assert.equal(progress[0].next_cursor,'next');
 const cursors=[];await runIndexBatches(async(cursor)=>{cursors.push(cursor);return {processed:1,results:[],next_cursor:null,done:true,current_only:true,remaining:0}},()=>false,()=>{},'resume');assert.deepEqual(cursors,['resume']);
 console.log('본문 검색 표시·관리 권한·중지·재개 검사 통과');
})().catch(e=>{console.error(e);process.exitCode=1});
