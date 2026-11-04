// Synthetic API UI regression; real provider/auth authorization covered separately.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const assert = require("node:assert/strict");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  const context = await browser.newContext();
  const base = process.env.FRONTEND_TEST_URL || "http://localhost:3000";
  const cid = "a".repeat(24);
  const user = { user_id: "synthetic", display_name: "Test", email: "synthetic@example.com", role: "employee", status: "active", department_id: "department" };
  const auth = { user, access_token: "synthetic-token", token_type: "bearer", expires_in: 600, csrf_token: "a".repeat(64), session_expires_at: new Date(Date.now()+3600000).toISOString() };
  let conversations = [], turns = [], mode = "ok", streamCalls = 0, cancellations = 0;
  const errors = [];
  const citation = { source_id: "S1", document_id: "b".repeat(24), filename: "synthetic.pdf", page: 1, source_path: `/documents/${"b".repeat(24)}/source` };
  const sse = (event, data) => `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  await context.route("**/api/v1/**", async route => {
    const req=route.request(), path=new URL(req.url()).pathname, method=req.method();
    let data={}, status=200;
    const headers={"Access-Control-Allow-Origin":base,"Access-Control-Allow-Credentials":"true","Access-Control-Allow-Headers":"Authorization,Content-Type,X-CSRF-Token","Access-Control-Allow-Methods":"GET,POST,PATCH,DELETE,OPTIONS"};
    if(method==="OPTIONS") data={};
    else if(path.endsWith("/auth/csrf")) data={csrf_token:auth.csrf_token};
    else if(path.endsWith("/auth/refresh")) data=auth;
    else if(path.endsWith("/users/me")) data=user;
    else if(path.endsWith("/messages/stream")) {
      streamCalls++;
      assert.equal(req.headers().authorization,"Bearer synthetic-token");
      const payload=JSON.parse(req.postData());
      assert.match(payload.client_request_id,/^[a-f0-9-]{36}$/);
      const turn={turn_id:String(streamCalls),client_request_id:payload.client_request_id,question:payload.question,answer:"Cedar provides 18 days [S1].",citations:[citation],status:"COMPLETED",is_complete:true};
      let body=sse("started",{client_request_id:payload.client_request_id})+sse("token",{text:"Cedar provides "});
      if(mode==="ok") { turns.push(turn); body+=sse("token",{text:"18 days [S1]."})+sse("citations",{citations:[citation]})+sse("completed",{is_complete:true}); }
      else if(mode==="quota") { turns.push({...turn,answer:"",citations:[],status:"INTERRUPTED",is_complete:false}); body+=sse("error",{code:"CHAT_RATE_LIMITED",message:"Synthetic quota exhausted",retry_after:2,request_id:"safe-request-123"}); }
      else if(mode==="disconnect") { turns.push({...turn,answer:"",citations:[],status:"INTERRUPTED",is_complete:false}); }
      else if(mode==="stop") { await new Promise(resolve=>setTimeout(resolve,3000)); body+=sse("error",{code:"CHAT_CANCELLED",message:"Stopped"}); }
      await route.fulfill({status:200,contentType:"text/event-stream",headers,body}).catch(()=>{});return;
    }
    else if(path.includes("/generations/") && path.endsWith("/cancel")) {cancellations++;status=204;}
    else if(path.endsWith("/messages")) {
      const cursor=new URL(req.url()).searchParams.get("cursor");
      const end=cursor ? turns.findIndex(turn=>turn.turn_id===cursor) : turns.length;
      const start=Math.max(0,end-20);
      data={turns:turns.slice(start,end),next_cursor:start>0?turns[start].turn_id:null};
    }
    else if(path.endsWith("/conversations") && method==="GET") data={conversations,next_cursor:null};
    else if(path.endsWith("/conversations") && method==="POST") {data={conversation_id:cid,title:"New conversation"};conversations=[data];status=201;}
    else if(path.endsWith(cid) && method==="PATCH") {conversations[0].title=JSON.parse(req.postData()).title;data=conversations[0];}
    else if(path.endsWith(cid) && method==="DELETE") {conversations=[];turns=[];status=204;}
    else if(path.endsWith("/source")) {status=404;data={error:{code:"DOCUMENT_NOT_FOUND",message:"Document was not found"}};turns=turns.map(turn=>({...turn,answer:"",citations:[],is_complete:false,status:"HIDDEN"}));}
    else throw new Error(`Unexpected ${method} ${path}`);
    await route.fulfill({status,contentType:"application/json",headers,body:status===204?"":JSON.stringify(data)});
  });
  const page=await context.newPage();
  page.on("pageerror", error=>errors.push(error.message));
  page.setDefaultTimeout(30000);
  try {
    await page.goto(base+"/chat");
    await page.getByRole("button",{name:"New conversation",exact:true}).click();
    const question=page.getByLabel("Your question",{exact:true});
    await question.fill("How many leave days?");
    await page.getByRole("button",{name:"Send question",exact:true}).click();
    await page.getByText("Cedar provides 18 days [S1].",{exact:true}).waitFor();
    await page.getByRole("button",{name:"S1: synthetic.pdf · Page 1",exact:true}).waitFor();
    assert.equal(await page.getByText("Assistant — provisional",{exact:true}).count(),0);
    await page.getByRole("button",{name:"S1: synthetic.pdf · Page 1",exact:true}).click();
    await page.getByText("Answer hidden because its source access or version changed.",{exact:true}).waitFor();
    assert.equal(await page.getByText("Cedar provides 18 days [S1].",{exact:true}).count(),0);
    mode="quota";
    await question.fill("Another question");
    await page.getByRole("button",{name:"Send question",exact:true}).click();
    await page.getByText("Synthetic quota exhausted",{exact:true}).waitFor();
    await page.getByText("Request ID: safe-request-123",{exact:true}).waitFor();
    await page.getByText("Retry in 2 seconds.",{exact:true}).waitFor();
    assert.equal(await page.getByRole("button",{name:"Send question",exact:true}).isDisabled(),true);
    await page.waitForTimeout(2300);
    mode="disconnect";
    await page.getByRole("button",{name:"Send question",exact:true}).click();
    await page.getByText("Connection ended before the answer completed. Reload the conversation to check its status.",{exact:true}).waitFor();
    mode="stop";
    await question.fill("Stop this request");
    await page.getByRole("button",{name:"Send question",exact:true}).click();
    await page.getByRole("button",{name:"Stop generation",exact:true}).click();
    await page.getByText("Stop requested. An answer completed before cancellation may still be saved.",{exact:true}).waitFor();
    await page.waitForTimeout(300);
    assert.equal(cancellations,1);
    await page.getByLabel("Conversation title",{exact:true}).fill("Renamed synthetic chat");
    await page.getByRole("button",{name:"Rename",exact:true}).click();
    await page.getByRole("button",{name:"Renamed synthetic chat",exact:true}).waitFor();
    turns=Array.from({length:45},(_,i)=>({turn_id:String(i+1),client_request_id:String(i+1),question:`History question ${i+1}`,answer:`History answer ${i+1}`,citations:[],status:"COMPLETED",is_complete:true}));
    await page.goto(base+"/chat?conversation="+cid);
    await page.getByText("History question 45",{exact:true}).waitFor();
    await page.getByRole("button",{name:"Load older messages",exact:true}).click();
    await page.getByText("History answer 6",{exact:true}).waitFor();
    turns=turns.map(turn=>turn.turn_id==="6"?{...turn,answer:"",status:"HIDDEN",is_complete:false}:turn);
    await page.evaluate(()=>window.dispatchEvent(new Event("focus")));
    await page.getByText("History answer 6",{exact:true}).waitFor({state:"detached"});
    assert.equal(await page.getByText("History question 6",{exact:true}).count(),1);
    assert.equal(await page.locator(".chat-turn").count(),40);
    await page.waitForTimeout(16000);
    assert.equal(await page.getByText("History question 6",{exact:true}).count(),1);
    assert.equal(await page.locator(".chat-turn").count(),40);
    await page.getByRole("button",{name:"Load older messages",exact:true}).click();
    await page.getByText("History question 1",{exact:true}).waitFor();
    assert.equal(await page.locator(".chat-turn").count(),45);
    await page.goto(base+"/history");
    await page.getByRole("link",{name:"Renamed synthetic chat",exact:true}).click();
    await question.waitFor();
    page.once("dialog",dialog=>dialog.accept());
    await page.getByRole("button",{name:"Delete conversation",exact:true}).click();
    await page.getByText("No conversations yet.",{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
    console.log("PASS: chat create/send/citations; revoked source hides answer; quota countdown; truncated stream clears partial; Stop/cancel; rename/history/delete; no browser errors or stored tokens");
  } finally {await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
