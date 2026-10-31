// Real rendered components with synthetic API responses; no application accounts.
// Requires Playwright, supplied through NODE_PATH or PLAYWRIGHT_MODULE_PATH.
// This is a standalone Node/browser harness, not a client-bundled module.
// eslint-disable-next-line @typescript-eslint/no-require-imports
const assert = require('node:assert/strict');
// eslint-disable-next-line @typescript-eslint/no-require-imports
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.BROWSER_EXECUTABLE ? { executablePath: process.env.BROWSER_EXECUTABLE } : {}),
  });
  const context = await browser.newContext();
  const errors = [];
  const requests = [];
  context.on('page', page => page.on('pageerror', error => errors.push(error.message)));
  const base = process.env.FRONTEND_TEST_URL || 'http://localhost:3000';
  const user = { user_id: 'review-admin', email: 'synthetic@example.com', display_name: 'Review Admin', role: 'admin', status: 'active', department_id: 'review-department' };
  const payload = { user, access_token: 'synthetic-memory-token', token_type: 'bearer', expires_in: 600, csrf_token: 'a'.repeat(64), session_expires_at: new Date(Date.now() + 3600000).toISOString() };
  const docs = Array.from({ length: 101 }, (_, index) => ({ document_id: `doc-${index + 1}`, filename: `Synthetic document ${index + 1}.txt`, size_bytes: 100, mime_type: 'text/plain', uploaded_by: user.user_id, visibility: 'PRIVATE', status: 'PROCESSING', active_index_version: null, chunk_count: 0, ingestion_status: 'PROCESSING', ingestion_stage: 'EXTRACTING', created_at: new Date().toISOString() }));
  const jobs = Array.from({ length: 101 }, (_, index) => ({ job_id: `job-${index + 1}`, document_id: `doc-${index + 1}`, status: 'FAILED', stage: 'INDEXING', attempt_count: 3, max_attempts: 3, error_code: 'TEST_FAILURE', error_message: 'Synthetic test failure', index_cleanup_status: null, index_cleanup_error_code: null, created_at: new Date().toISOString(), updated_at: new Date().toISOString() }));

  await context.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    requests.push(url.pathname + url.search);
    let data;
    if (url.pathname.endsWith('/auth/csrf')) data = { csrf_token: payload.csrf_token };
    else if (url.pathname.endsWith('/auth/refresh')) data = payload;
    else if (url.pathname.endsWith('/users/me')) data = user;
    else if (url.pathname.endsWith('/documents')) {
      const offset = Number(url.searchParams.get('cursor') || 0);
      data = { documents: docs.slice(offset, offset + 50), next_cursor: offset + 50 < docs.length ? String(offset + 50) : null };
    } else if (url.pathname.endsWith('/admin/ingestion-jobs')) {
      const offset = Number(url.searchParams.get('cursor') || 0);
      data = { jobs: jobs.slice(offset, offset + 50), next_cursor: offset + 50 < jobs.length ? String(offset + 50) : null };
    } else throw new Error('Unexpected synthetic API request: ' + url.pathname);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data), headers: { 'Access-Control-Allow-Origin': base, 'Access-Control-Allow-Credentials': 'true', 'Access-Control-Allow-Headers': 'Authorization,Content-Type,X-CSRF-Token,Idempotency-Key', 'Access-Control-Allow-Methods': 'GET,POST,PATCH,DELETE,OPTIONS' } });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(60000);
  try {
    await page.goto(base + '/documents');
    await page.getByText('Synthetic document 1.txt', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Next documents' }).click();
    await page.getByText('Synthetic document 51.txt', { exact: true }).waitFor();
    const polled = page.waitForResponse(response => response.url().includes('/documents?') && response.url().includes('cursor=50'));
    await polled; // automatic pending-document polling must retain page 2
    await page.getByText('Synthetic document 51.txt', { exact: true }).waitFor();
    assert.equal(await page.getByText('Synthetic document 1.txt', { exact: true }).count(), 0);
    await page.getByRole('button', { name: 'Next documents' }).click();
    await page.getByText('Synthetic document 101.txt', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Next documents' }).isDisabled(), true);
    await page.getByRole('button', { name: 'Previous documents' }).click();
    await page.getByText('Synthetic document 51.txt', { exact: true }).waitFor();
    console.log('PASS: document cursor navigation reaches row 101 and polling preserves the selected page');

    await page.goto(base + '/admin/ingestion-jobs');
    await page.getByText('Job job-1 · Document doc-1', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Next jobs' }).click();
    await page.getByText('Job job-51 · Document doc-51', { exact: true }).waitFor();
    await page.getByRole('button', { name: 'Next jobs' }).click();
    await page.getByText('Job job-101 · Document doc-101', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Retry', exact: true }).count(), 1);
    assert.equal(await page.getByRole('button', { name: 'Next jobs' }).isDisabled(), true);
    await page.getByRole('button', { name: 'Previous jobs' }).click();
    await page.getByText('Job job-51 · Document doc-51', { exact: true }).waitFor();
    await page.getByLabel('Status', { exact: true }).selectOption('FAILED');
    await page.getByText('Job job-1 · Document doc-1', { exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: 'Previous jobs' }).isDisabled(), true);
    assert(requests.some(url => url.includes('status=FAILED') && !url.includes('cursor=')));
    assert.deepEqual(errors, []);
    console.log('PASS: older failed jobs remain reachable for Retry; status filters reset pagination; no browser errors');
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
