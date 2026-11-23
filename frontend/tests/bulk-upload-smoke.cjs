// Rendered bulk upload regression using synthetic API responses.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const assert = require("node:assert/strict");
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || "playwright");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}),
  });
  const context = await browser.newContext();
  const base = process.env.FRONTEND_TEST_URL || "http://localhost:3100";
  const errors = [];
  let role = "manager";
  const uploadAttempts = new Map();
  const accepted = new Map();
  const jobs = new Map();
  let activeUploads = 0;
  let peakUploads = 0;
  let processingRetries = 0;
  let refreshes = 0;
  let transientStatusFailure = true;
  let ocrStatusShown = false;
  const user = () => ({
    user_id: "a".repeat(24), display_name: "Synthetic Manager", email: "synthetic@example.com",
    role, status: "active", department_id: "b".repeat(24),
  });
  const makeJob = (id, failed = false) => ({
    job_id: id, document_id: id, status: failed ? "FAILED" : "SUCCEEDED",
    stage: failed ? "EMBEDDING" : "COMPLETE", attempt_count: 1, max_attempts: 3,
    error_code: failed ? "EMBEDDING_UNAVAILABLE" : null,
    error_message: failed ? "Synthetic embedding failure" : null,
  });
  context.on("page", page => {
    page.on("pageerror", error => errors.push(error.message));
    page.on("console", message => {
      if (message.type() === "error" && message.text().includes("Encountered two children")) {
        errors.push(message.text());
      }
    });
  });
  await context.route("**/api/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "");
    let data = {}, status = 200;
    if (request.method() === "OPTIONS") data = {};
    else if (path === "/auth/csrf") data = { csrf_token: "c".repeat(64) };
    else if (path === "/auth/refresh") {
      refreshes++;
      data = {
        user: user(), access_token: "synthetic-token", token_type: "bearer", expires_in: 600,
        csrf_token: "c".repeat(64), session_expires_at: new Date(Date.now() + 3600000).toISOString(),
      };
    }
    else if (path === "/users/me") data = user();
    else if (path === "/documents" && request.method() === "GET") data = {
      documents: [], next_cursor: new URL(request.url()).searchParams.has("cursor") ? null : "next-doc-page",
    };
    else if (path === "/documents" && request.method() === "POST") {
      assert.equal(request.headers().authorization, "Bearer synthetic-token");
      const body = request.postDataBuffer().toString();
      const filename = /filename="([^"]+)"/.exec(body)?.[1];
      assert.ok(filename, "Upload includes a filename");
      assert.match(body, /name="visibility"\r\n\r\nPRIVATE/);
      const key = request.headers()["idempotency-key"];
      assert.match(key, /^[0-9a-f-]{36}$/);
      const keys = uploadAttempts.get(filename) || [];
      keys.push(key);
      uploadAttempts.set(filename, keys);
      activeUploads++;
      peakUploads = Math.max(peakUploads, activeUploads);
      await new Promise(resolve => setTimeout(resolve, filename === "alpha.txt" ? 800 : 350));
      activeUploads--;
      if (filename === "missing-ids.txt" && keys.length === 1) {
        await route.fulfill({ status: 202, contentType: "application/json", body: "{}",
          headers: { "Access-Control-Allow-Origin": base, "Access-Control-Allow-Credentials": "true" } });
        return;
      }
      if (filename === "empty-response.txt" && keys.length === 1) {
        await route.fulfill({ status: 204, headers: {
          "Access-Control-Allow-Origin": base, "Access-Control-Allow-Credentials": "true",
        } });
        return;
      }
      if (filename === "auth.txt" && keys.length === 1) {
        status = 401;
        data = { error: { code: "SESSION_EXPIRED", message: "Refresh the session" } };
      } else if (filename === "rate.txt") {
        status = 429;
        data = { error: { code: "RATE_LIMITED", message: "Upload limit reached", request_id: "synthetic-limit" } };
      } else {
        if (!accepted.has(key)) {
          const id = String(accepted.size + 1).padStart(24, "0");
          accepted.set(key, { document_id: id, job_id: id, status: "QUEUED" });
          jobs.set(id, makeJob(id, filename === "process.txt"));
        }
        if (filename === "retry.txt" && keys.length === 1) {
          await route.abort("failed");
          return;
        }
        status = 202;
        data = accepted.get(key);
      }
    } else if (path.startsWith("/ingestion-jobs/")) {
      const id = path.split("/").at(-1);
      if (id === "1".padStart(24, "0") && transientStatusFailure) {
        transientStatusFailure = false;
        status = 503;
        data = { error: { code: "UNAVAILABLE", message: "Synthetic status interruption" } };
      } else if (id === "1".padStart(24, "0") && !ocrStatusShown) {
        ocrStatusShown = true;
        data = {
          ...jobs.get(id), status: "PROCESSING", stage: "OCR",
          ocr_current_page: 2, ocr_pages_completed: 0, ocr_pages_total: 1,
        };
      } else data = jobs.get(id);
    } else if (/^\/documents\/[^/]+\/jobs$/.test(path)) {
      const id = path.split("/")[2];
      data = { jobs: [jobs.get(id)] };
    } else if (path.endsWith("/reprocess")) {
      processingRetries++;
      const id = path.split("/")[2];
      jobs.set(id, { ...makeJob(id), job_id: "9".repeat(24) });
      // The backend accepted this retry. Drop the response to exercise reconciliation.
      await route.abort("failed");
      return;
    } else if (path === "/retrieval/readiness") {
      data = { status: "ready", model: "ready", vectors: "ready", code: null };
    } else throw new Error("Unexpected request " + request.method() + " " + path);
    await route.fulfill({
      status, contentType: "application/json", body: JSON.stringify(data),
      headers: {
        "Access-Control-Allow-Origin": base, "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Headers": "Authorization,Content-Type,Idempotency-Key,X-CSRF-Token",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Expose-Headers": "Retry-After,X-Request-ID",
        ...(status === 429 ? { "Retry-After": "30" } : {}),
      },
    });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  try {
    await page.goto(base + "/manager/upload");
    const choose = page.getByLabel("Choose files", { exact: true });
    await choose.waitFor();
    assert.equal(await choose.getAttribute("multiple"), "");
    assert.equal(await page.getByRole("option", { name: "Organization · everyone", exact: true }).count(), 0);
    await page.getByLabel("Visibility for selected files").selectOption("PRIVATE");
    await choose.setInputFiles([
      { name: "alpha.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic alpha") },
      { name: "retry.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic retry") },
      { name: "process.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic process") },
      { name: "invalid.exe", mimeType: "application/octet-stream", buffer: Buffer.from("Synthetic invalid") },
      { name: "empty.txt", mimeType: "text/plain", buffer: Buffer.alloc(0) },
      { name: "large.txt", mimeType: "text/plain", buffer: Buffer.alloc(20 * 1024 * 1024 + 1) },
    ]);
    await page.getByRole("button", { name: "Upload 6 files", exact: true }).click();
    await page.getByRole("progressbar", { name: "Uploading alpha.txt", exact: true }).waitFor();
    await page.getByRole("button", { name: "Next documents", exact: true }).click();
    const row = name => page.getByRole("listitem").filter({ has: page.getByText(name, { exact: true }) });
    await row("retry.txt").getByRole("button", { name: "Retry upload", exact: true }).waitFor();
    await row("process.txt").getByRole("button", { name: "Retry processing", exact: true }).waitFor();
    await row("alpha.txt").getByText("PDF page 2 · 0 of 1 scanned pages read", { exact: true }).waitFor();
    await row("alpha.txt").getByText("Ready for search", { exact: true }).waitFor();
    await page.getByRole("navigation", { name: "Document pages" }).getByText("Page 1", { exact: true }).waitFor();
    assert.equal(accepted.size, 3);
    assert.equal(peakUploads, 1);
    assert.equal(uploadAttempts.size, 3);
    await row("invalid.exe").getByText("Choose a PDF, DOCX, or TXT file.", { exact: true }).waitFor();
    await row("empty.txt").getByText("This file is empty.", { exact: true }).waitFor();
    await row("large.txt").getByText("Files must be 20 MB or smaller.", { exact: true }).waitFor();
    await page.getByLabel("Visibility for selected files").selectOption("DEPARTMENT");
    await row("retry.txt").getByRole("button", { name: "Retry upload", exact: true }).click();
    await row("retry.txt").getByText("Ready for search", { exact: true }).waitFor();
    const retriedKeys = uploadAttempts.get("retry.txt");
    assert.equal(retriedKeys.length, 2);
    assert.equal(retriedKeys[0], retriedKeys[1], "Retry reuses the original upload identity");
    assert.equal(accepted.size, 3, "Lost response retry creates no duplicate document");
    await row("process.txt").getByRole("button", { name: "Retry processing", exact: true }).click();
    await row("process.txt").getByRole("alert").waitFor();
    await row("process.txt").getByRole("button", { name: "Retry processing", exact: true }).click();
    await row("process.txt").getByText("Ready for search", { exact: true }).waitFor();
    assert.equal(processingRetries, 1, "A lost reprocess response is recovered through job history");
    assert.equal(uploadAttempts.get("process.txt").length, 1, "Processing retry uses the stored file");
    const refreshesBefore = refreshes;
    await page.getByLabel("Visibility for selected files").selectOption("PRIVATE");
    await choose.setInputFiles([
      { name: "auth.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic expired auth") },
      { name: "empty-response.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic empty response") },
      { name: "missing-ids.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic incomplete acceptance") },
      { name: "rate.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic rate limit") },
      { name: "paused.txt", mimeType: "text/plain", buffer: Buffer.from("Synthetic paused file") },
    ]);
    await page.getByRole("button", { name: "Upload 5 files", exact: true }).click();
    await row("auth.txt").getByText("Ready for search", { exact: true }).waitFor();
    assert.equal(refreshes, refreshesBefore + 1);
    const authKeys = uploadAttempts.get("auth.txt");
    assert.equal(authKeys.length, 2);
    assert.equal(authKeys[0], authKeys[1]);
    await row("rate.txt").getByText(/Retry after 30 seconds/).waitFor();
    assert.equal(uploadAttempts.get("rate.txt").length, 1, "Rate limits require an explicit retry");
    assert.equal(uploadAttempts.has("paused.txt"), false, "No file is sent after a rate limit");
    await row("paused.txt").getByText(/Batch paused/).waitFor();
    await row("missing-ids.txt").getByRole("button", { name: "Retry upload", exact: true }).click();
    await row("missing-ids.txt").getByText("Ready for search", { exact: true }).waitFor();
    assert.equal(uploadAttempts.get("missing-ids.txt")[0], uploadAttempts.get("missing-ids.txt")[1]);
    await row("empty-response.txt").getByRole("button", { name: "Retry upload", exact: true }).click();
    await row("empty-response.txt").getByText("Ready for search", { exact: true }).waitFor();
    assert.equal(uploadAttempts.get("empty-response.txt")[0], uploadAttempts.get("empty-response.txt")[1]);

    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), "No mobile overflow");
    if (process.env.BULK_SCREENSHOT_PATH) await page.screenshot({ path: process.env.BULK_SCREENSHOT_PATH, fullPage: true });
    role = "employee";
    await page.reload();
    await page.getByRole("heading", { name: "You don’t have access to this page" }).waitFor();
    assert.equal(await page.getByLabel("Choose files", { exact: true }).count(), 0);
    role = "admin";
    await page.reload();
    await page.getByLabel("Choose files", { exact: true }).waitFor();
    assert.equal(await page.getByRole("option", { name: "Organization · everyone", exact: true }).count(), 1);
    await page.goto(base + "/documents");
    await page.getByLabel("Choose files", { exact: true }).waitFor();
    assert.deepEqual(errors, []);
    console.log("Bulk upload browser regression passed");
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
