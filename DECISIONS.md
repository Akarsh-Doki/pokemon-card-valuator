# Design Decisions

This page records the significant choices in the project. For each one it explains what was decided, why it was decided, and which alternatives were rejected.

## 1. Detect regions first, then run OCR

**Decision.** A YOLOv8 detector crops the card down to three regions, which are the title, the card number, and the set symbol. OCR then runs only on those crops and never on the full photo.

**Why.** The first version ran OCR directly on the whole photo and was only about 30 percent accurate. A card photo is mostly irrelevant area such as background, art, and borders, and OCR gets worse when an image is noisy and carries little useful text. Cropping to the small and predictable regions first raised the accuracy of the reads to about 85 percent.

**Rejected.** Running OCR on the raw image was rejected because it was too inaccurate. A single classification model that maps a whole photo straight to a card was also rejected, because it would need far more labeled data and still could not read the printed number or set.

## 2. Use several OCR engines with a fallback order

**Decision.** PaddleOCR is the primary OCR engine. EasyOCR and pytesseract act as fallbacks, and the system reaches for them when PaddleOCR returns low confidence or fails to run.

**Why.** OCR on stylized card fonts is fragile, and any single engine misreads some cards. A fallback order means that when one engine fails on a field, another engine can still recover it, so a single failure does not ruin the whole scan.

**Rejected.** Relying on one engine was rejected because it is less robust. Training a custom OCR model was also rejected, because the existing engines already handle printed text well enough that a custom model would not be worth the effort.

## 3. Break ambiguous matches with a color histogram

**Decision.** After the OCR text is matched against the card database, the system checks whether several printings share the same name and number. When they do, it compares the RGB colour histogram of the scanned card against each candidate and picks the closest one.

**Why.** Text alone does not always settle a card's identity. The same card can exist in several printings that share an identical name and number but differ in their art and colour. A simple colour histogram comparison tells those printings apart, and it does so without the cost of large visual embedding models.

**Rejected.** Matching on text alone was rejected because it cannot separate printings that share the same text. Using large CLIP style image embeddings was also rejected, because the cards are visually distinct enough that a colour histogram is already sufficient.

## 4. Use Server-Sent Events for progress instead of WebSockets

**Decision.** Scan progress, meaning the steps for detecting, reading, matching, and pricing, is streamed to the user interface over Server-Sent Events.

**Why.** The progress stream only ever flows in one direction, from the server to the client. Server-Sent Events provide exactly that over plain HTTP, with built in browser support and automatic reconnection. They are also simpler to operate than a two way WebSocket connection, which this feature does not need.

**Rejected.** WebSockets were rejected because they are bidirectional and add moving parts the app does not require. Polling was also rejected because it feels laggy and wastes requests.

## 5. Version data and models with DVC instead of committing them to git

**Decision.** The training data and the model files are versioned with DVC rather than committed into the git repository.

**Why.** Large binary files make a git repository bloated and slow to clone, and git is not built to compare them. DVC tracks those files separately while keeping the pipeline reproducible.

**Rejected.** Committing the data and models into git was rejected because it bloats the repository and slows down cloning. Keeping no versioning at all was also rejected, because it would make experiments impossible to reproduce.

## 6. Run heavy CPU work in an executor

**Decision.** The YOLO detection and the OCR steps are heavy on the processor, so they run in an executor pool through `run_in_executor` instead of running directly inside the async FastAPI handler.

**Why.** A long computation inside the single threaded async event loop would block every other request, including the live progress stream. Moving that work into an executor keeps the event loop free to keep serving requests and streaming progress.

**Rejected.** Running the processor work directly inside the async handler was rejected because it freezes the server while the work runs. Adding a separate worker service was also rejected, because it is more infrastructure than a project of this size needs.
