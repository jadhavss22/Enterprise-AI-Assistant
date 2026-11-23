# OCR for printed English PDFs

PDF ingestion uses local Tesseract on the Linux worker for pages that need text recognition. OCR is enabled by default in the updated backend image. Rebuild and restart the backend services together to deploy the new engine, ingestion stage, and job metadata.

```sh
docker compose build api worker scheduler
docker compose up -d api worker scheduler
```

The image includes Tesseract, English data, and orientation detection data. The worker invokes automatic page segmentation with orientation detection, renders grayscale pages at up to 200 DPI, and recognizes printed English. See the [Tesseract command-line documentation](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).

## Page selection and citations

Native text extraction runs first. Pages with fewer than 200 native alphanumeric characters and significant image coverage are selected for OCR. Nonempty page content with no native text also receives OCR. Native text is retained on mixed pages, and exact repeated lines from OCR are removed. Blank raster pages can contribute no text. A nonblank scanned page with no readable result, or character-weighted mean word confidence below 60, fails the job with an explicit error.

Page numbers remain one-based positions in the original PDF. Each recognized page feeds the existing page-specific chunker, and chunk metadata carries that original page through indexing, retrieval, and answer citations. The original uploaded PDF is the citation source.

## Limits and isolation

The existing 20 MB upload limit, 2,000 PDF-page limit, and 10 million extracted-character limit apply. Additional OCR defaults are configured in the worker environment.

| Setting | Default |
|---|---|
| DOCUMENT_OCR_ENABLED | true |
| DOCUMENT_OCR_MAX_PAGES | 50 scanned pages |
| DOCUMENT_OCR_PAGE_TIMEOUT_SECONDS | 20 seconds |
| DOCUMENT_OCR_TIMEOUT_SECONDS | 120 seconds per OCR document stage |
| DOCUMENT_OCR_MAX_PIXELS | 8 million pixels per render |
| DOCUMENT_OCR_MEMORY_MB | 768 MiB per child |

Each page uses a fresh Linux process group for rendering and recognition, with address-space, CPU, and file-size limits. Tesseract uses one OpenMP thread. Page images have a maximum side length of 6,000 pixels, TSV input to the parser is bounded, and recognized text is limited to 50,000 characters per page. The parent kills the process group and reaps its helper on every exit, including timeouts and abnormal helper termination. The child also kills its process group if the parent dies. Temporary PDFs, images, and recognition output are removed during normal success and error cleanup. Child stderr is suppressed, and errors expose fixed codes and messages.

## Progress and retries

Jobs report the OCR stage and ocr_current_page, ocr_pages_completed, and ocr_pages_total. The upload queue and Admin ingestion jobs display these counters. Progress updates renew the fenced worker lease and check source deletion. Lease-loss and deletion signals pass through extraction to the worker cancellation handler. Retries reset the counters.

Engine unavailability, engine errors, and per-page timeouts use the existing maximum of three automatic ingestion attempts. Unreadable scans, page-count limits, document deadlines, and resource-limit failures terminate the attempt with a clear error. If a page exhausts the remaining document budget, it is classified as a document deadline rather than a retryable per-page timeout. The existing reprocess/retry routes can create another job after the worker is updated or a transient failure is resolved. Clearer replacement files use the normal upload flow.

Extraction finishes before any new vectors are written. Failed OCR cannot activate a partially extracted version. Existing active versions follow the current transactional activation and cleanup rules.

## Verification

The test_ocr module generates synthetic PDFs in memory and covers native text, scanned and mixed pages, 0/90/180/270-degree rotations, unreadable and blank scans, confidence filtering, render budgets, wall deadlines, and process-group cleanup.

The test_ocr_ingestion module exercises upload, actual OCR, ingestion, vector metadata, authenticated search, and Chat page citations against disposable MongoDB state. Embedding, vector storage, blob storage, and answer generation use doubles. Other tests check retry exhaustion, active-version preservation, and lease fencing.

CI installs the OCR packages and sets RUN_OCR_TESTS=1, which requires the real Linux OCR fixtures to run. Native development environments without that engine can run the other tests. Manual acceptance with representative real company scans remains part of rollout.


Backend CI targets Ubuntu 24.04 with real OCR required. The Debian-based backend image is the tested deployment runtime. Ubuntu 26.04 hosted images produced different OCR behavior across providers during review, so validate a new OS/runtime before adopting it. The per-page and per-document processing limits apply throughout.
