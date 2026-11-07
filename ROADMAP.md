# Journal Analytics Roadmap

Journal Analytics ingests handwritten and typed journal entries, scores their mood, and allows the owner to search, chart, summarise and question their own writing. A working multi-user version already exists. However, it will not be deployed in its current form, because it relies on a central server to store and process some of the most private writing a person produces.

## Aim

This project aims to rebuild the application around a local-first design. Journal data will be processed on the user's own device wherever possible, and anything that leaves the device for storage will be encrypted first. No decrypted journal data will be stored on infrastructure run by the project.

## Open questions

The route to this goal is not yet settled. Handwriting recognition is the central problem. Accurate OCR currently depends on a cloud model, which sits uneasily with the aim above. Furthermore, users should not pay to process the same page twice, yet their OCR results should not be held in a central database.

Syncing encrypted results to a private account owned by the user is one possible answer, but it requires investigation. Other questions concern key management and recovery, whether search and embeddings can run acceptably on phones, and how any use of an external model should be disclosed to users.

## Phase 1: Research and design

The first phase will review the options for on-device processing, OCR and user-owned storage, then rethink encryption, the data processing flow and the privacy model. The outcome will be a design for the new architecture.

## Phase 2: Prototyping

The riskiest parts of the design will be tested in small prototypes, including local search, on-device embeddings and encrypted sync. These results will determine which approaches are carried forward.

## Phase 3: Rebuild

The core features will then be rebuilt on the new architecture, and the server-side components that are no longer required will be removed.

## Phase 4: Testing and release

A small group of users will test the application before release. Their feedback, together with an independent review of the encryption, will shape the direction of the work that follows.
