// Rendered search UI regression with synthetic API responses; no real accounts.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const assert = require("node:assert/strict");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}) });
  const context = await browser.newContext();
  const errors = [];
  const base = process.env.FRONTEND_TEST_URL || "http://localhost:3000";
  const user = { user_id: "synthetic-employee", display_name: "Test Employee", email: "synthetic@example.com", role: "employee", status: "active", department_id: "synthetic-department" };
  const auth = { user, access_token: "synthetic-memory-token", token_type: "bearer", expires_in: 600, csrf_token: "a".repeat(64), session_expires_at: new Date(Date.now()+3600000).toISOString() };
  let mode = "match";
  let searches = 0;
  context.on("page", page => page.on("pageerror", error => errors.push(error.message)));
  await context.route("**/api/v1/**", async route => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    let data, status = 200;
    if (req.method() === "OPTIONS") data = {};
    else if (path.endsWith("/auth/csrf")) data = { csrf_token: auth.csrf_token };
    else if (path.endsWith("/auth/refresh")) data = auth;
    else if (path.endsWith("/users/me")) data = user;
    else if (path.endsWith("/documents")) data = { documents: [], next_cursor: null };
    else if (path.endsWith("/retrieval/search")) {
      searches++;
      assert.equal(req.method(), "POST");
      assert.equal(req.headers().authorization, "Bearer synthetic-memory-token");
      if (mode === "failure") { status = 503; data = { error: { code: "SEARCH_UNAVAILABLE", message: "Search temporarily unavailable" } }; }
      else if (mode === "long") { status = 422; data = { error: { code: "QUERY_TOO_LONG", message: "Shorten the question" } }; }
      else data = { status: mode === "empty" ? "NO_RELEVANT_MATCHES" : mode === "no-documents" ? "NO_ACCESSIBLE_DOCUMENTS" : "MATCHES", request_id: "synthetic-request", passages: mode !== "match" ? [] : [{ document_id: "synthetic-document", filename: "Synthetic support policy.pdf", index_version: 1, chunk_id: "synthetic-chunk", chunk_index: 0, page: 1, text: "Synthetic support operates Monday to Friday.", source_path: "/documents/synthetic-document/source" }] };
    } else if (path.endsWith("/source")) { status = 404; data = { error: { code: "DOCUMENT_NOT_FOUND", message: "Document was not found" } }; }
    else throw new Error("Unexpected synthetic API request: " + path);
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data), headers: { "Access-Control-Allow-Origin": base, "Access-Control-Allow-Credentials": "true", "Access-Control-Allow-Headers": "Authorization,Content-Type,X-CSRF-Token", "Access-Control-Allow-Methods": "GET,POST,OPTIONS" } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  try {
    await page.goto(base + "/documents");
    const button = page.getByRole("button", { name: "Search passages", exact: true });
    await button.waitFor();
    assert.equal(await button.isDisabled(), true);
    await page.getByLabel("Your question", { exact: true }).fill("What are the support hours?");
    await button.click();
    await page.getByText("Synthetic support operates Monday to Friday.", { exact: true }).waitFor();
    assert.equal(await page.locator(".search-passage").count(), 1);
    mode = "empty";
    await button.click();
    await page.getByText("No relevant passages were found. Try a more specific question.", { exact: true }).waitFor();
    assert.equal(await page.locator(".search-passage").count(), 0);
    mode = "no-documents";
    await button.click();
    await page.getByText("No searchable documents are available to your account yet.", { exact: true }).waitFor();
    mode = "failure";
    await button.click();
    await page.getByRole("alert").filter({ hasText: "Search temporarily unavailable" }).waitFor();
    mode = "long";
    await button.click();
    await page.getByRole("alert").filter({ hasText: "Shorten the question" }).waitFor();
    mode = "match";
    await button.click();
    await page.getByText("Synthetic support operates Monday to Friday.", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Download source", exact: true }).click();
    await page.getByRole("alert").filter({ hasText: "Document was not found" }).waitFor();
    assert.equal(await page.locator(".search-passage").count(), 0);
    assert.equal(searches, 6);
    assert.deepEqual(errors, []);
    console.log("PASS: authenticated search, source passages, empty/no-document states, provider/token errors and revoked download handling; no browser errors");
  } finally { await context.close(); await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
