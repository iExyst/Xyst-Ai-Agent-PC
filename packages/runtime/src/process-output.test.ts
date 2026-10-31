import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { XystStore, ConfigStore, SessionStore } from "./storage.js";
import { RuntimeEvents } from "./events.js";
import { AccessController } from "./access.js";
import { AgentRunner, assertRequiredToolEvidence } from "./agent.js";
import { runProcess } from "./platform.js";

test("large process output stays valid, redacted, recoverable, and replayable", async () => {
  const root=await mkdtemp(join(tmpdir(),"xyst-process-output-")); const store=new XystStore(join(root,"db.sqlite"));
  const config=new ConfigStore(store); const sessions=new SessionStore(store); const events=new RuntimeEvents();
  config.set("global","model.default","fake"); let call=0;
  const secret="sensitive-test-secret-12345";
  const source=`process.stdout.write('"'.repeat(5000)+${JSON.stringify(secret)}); process.stderr.write('e'.repeat(5000));`;
  const provider={ redact:(s:string)=>s.replaceAll(secret,"[REDACTED]"), streamChatWithTools:async()=>({choices:[{message:{role:"assistant",content:null,tool_calls:[{id:`process-${++call}`,type:"function",function:{name:"run_process",arguments:JSON.stringify({executable:process.execPath,args:["-e",source],cwd:root})}}]}}]}) };
  const runner=new AgentRunner({store,config,sessions,events,access:new AccessController(),provider:provider as never,github:{} as never});
  try {
    const first=await runner.run("Run the command.",{workspace:root,maxTurns:1});
    const saved=store.db.prepare("SELECT output_json FROM tool_calls WHERE name='run_process'").get() as {output_json:string};
    const result=JSON.parse(saved.output_json); assert.equal(result.code,0); assert.equal(result.signal,null);
    assert.equal(result.stdoutTruncated,true); assert.equal(result.stderrTruncated,true); assert.ok(saved.output_json.length<30000); assert.ok(!saved.output_json.includes(secret));
    assert.throws(()=>assertRequiredToolEvidence("run_process",result,{tool:"run_process",stdoutEquals:result.stdout}),/truncated/);
    assert.throws(()=>assertRequiredToolEvidence("run_process",result,{tool:"run_process",stdoutIncludes:'"'.repeat(4)}),/truncated/);
    const artifact=store.db.prepare("SELECT path FROM artifacts WHERE session_id=?").get(first.sessionId) as {path:string};
    assert.equal(artifact.path,result.outputArtifactPath); const full=JSON.parse(await readFile(artifact.path,"utf8"));
    assert.equal(full.stdout.length,5000+"[REDACTED]".length); assert.equal(full.stderr.length,5000); assert.ok(!JSON.stringify(full).includes(secret));
    await runner.run("Continue.",{workspace:root,sessionId:first.sessionId,maxTurns:1});
    assert.equal((store.db.prepare("SELECT COUNT(*) AS count FROM tool_calls WHERE name='run_process'").get() as {count:number}).count,1);
    assert.equal((store.db.prepare("SELECT COUNT(*) AS count FROM artifacts WHERE session_id=?").get(first.sessionId) as {count:number}).count,1);
  } finally {store.close(); await rm(root,{recursive:true,force:true});}
});

test("process capture reports truncation instead of silently dropping output", async () => {
  const result=await runProcess(process.execPath,["-e","process.stdout.write('abcdefgh'); process.stderr.write('ABCDEFGH')"],{maxOutputChars:4});
  assert.equal(result.stdout,"abcd"); assert.equal(result.stderr,"ABCD"); assert.equal(result.stdoutTruncated,true); assert.equal(result.stderrTruncated,true); assert.equal(result.code,0);
});
