# Challenge requirements

Source: *Technical Challenge — Supplier Invoice Three-Way Matching with Human Review*, Machines Like Me GmbH, September 2026.

This file restates the brief as checkable requirements. It is the reference for every plan, change and review. Section numbers (§) point to the original brief.

- When a requirement is ambiguous, the answer adopted is in the **decision log** of the README. The decision log wins over a literal reading of this file only where it records a client answer or a documented assumption.
- Credentials are **not** in this file and must never be committed. The API key lives only in `.env` locally and in the secret store in the cloud.

## Deadline

- The live demo is the deadline: **Thursday 15 October 2026, 16:00 (Buenos Aires, UTC-3)**.
- Commits pushed after that time are not considered. The repository and the deployed instance are reviewed as they are at that moment (§10.2).

## 1. Goal (§1, §2)

Contoso matches supplier invoices against the purchase order (PO) and the goods received notes (GRN) before paying them. The system must:

- **G1.** Extract structured data from an uploaded invoice (PDF or image), including line items.
- **G2.** Match the invoice against the referenced PO and its GRNs.
- **G3.** Assign exactly one automatic outcome: **Auto-Pay**, **Rejected** or **Held for Review**.
- **G4.** Let an AP analyst confirm, correct or override the outcome, with full traceability of who changed what, when and why.

## 2. Roles (§3)

### Intake user
- **R-IN1.** Uploads an invoice document and submits it for evaluation.
- **R-IN2.** Can see the status of each submitted invoice at any time.
- **R-IN3.** Can see the reason for a rejection or a hold, in terms an accounts clerk understands.
- **R-IN4.** Cannot modify reference data (POs, GRNs, supplier master).

### AP Analyst
- **R-AN1.** Can see the queue of held invoices and the history of all invoices.
- **R-AN2.** Can correct extracted invoice data when extraction errors are found.
- **R-AN3.** Can approve, reject or request a credit note for a held invoice, with a **mandatory comment**.
- **R-AN4.** Can override an automatic outcome **within the limits of the role**.

### AP Manager
- **R-MG1.** Has every capability of the AP Analyst.
- **R-MG2.** Is the **only** role that can approve an override where the invoice total exceeds the analyst approval limit.
- **R-MG3.** Can view and change the rule parameters.
- **R-MG4.** Can view the complete audit trail of any invoice.

## 3. Expected flow (§4)

1. The intake user uploads an invoice.
2. The system extracts header fields and line items.
3. The system finds the referenced PO(s) and their GRNs in the reference data.
4. The rule engine assigns exactly one outcome.
5. For held invoices, an analyst reviews, corrects data if needed, and finalises the decision with a comment.
6. Overrides above the analyst approval limit are escalated to the AP Manager.
7. Every automatic and human decision is recorded in an audit trail that can be inspected per invoice.

## 4. Reference data (§5, Appendix B)

- **D1.** Three JSON files: `suppliers.json`, `purchase_orders.json`, `goods_received_notes.json`. They are the **source of truth** for matching.
- **D2.** Must not be hard-coded and must not be editable from the intake interface.
- **D3.** The solution must include a **script or command that loads the reference data**. Reviewers will run it during the demo.
- **D4.** A PO belongs to exactly one supplier. A GRN belongs to exactly one PO. A PO may have zero, one or several GRNs; the quantity received for a PO line is the **sum across all its GRNs**.

Schemas:

| File | Fields |
|---|---|
| `suppliers.json` | `supplier_id`, `legal_name`, `tax_id`, `default_currency` (ISO 4217), `status` (`active` / `inactive`) |
| `purchase_orders.json` | `po_number`, `supplier_id`, `po_date`, `currency`, `status` (`open` / `partially_invoiced` / `closed`), `lines[]` with `line_number`, `item_code`, `description`, `quantity_ordered`, `unit_price` |
| `goods_received_notes.json` | `grn_number`, `po_number`, `received_date`, `lines[]` with `po_line_number`, `quantity_received` |

## 5. Extraction (§6)

Header fields: `supplier_name`, `supplier_tax_id`, `invoice_number`, `invoice_date`, `due_date` (if present), `po_references` (one or more), `currency`, `subtotal`, `tax_amount`, `total_amount`.

Line fields, one record per line: `description`, `quantity`, `unit_price`, `line_total`, `po_line_reference` (if determinable).

- **E1.** The quality of the mapping between invoice lines and PO lines directly affects the rule engine. The system must behave sensibly when the mapping **cannot be established with confidence**.

## 6. Rule engine (§7)

Exactly one outcome per invoice, in this order of precedence:

| Outcome | Condition | Precedence |
|---|---|---|
| **Rejected** | No PO referenced; **or** the PO does not exist; **or** the invoice supplier does not match the PO supplier; **or** the invoice is a duplicate of one already processed; **or** the invoice date is earlier than the PO date. | Evaluated first. Any condition rejects, regardless of amounts. |
| **Held for Review** | Not rejected **and** at least one of: a line's invoiced quantity exceeds the quantity received per the GRNs; a line's unit price differs from the PO beyond the configured tolerance; a line cannot be matched to a PO line. | Only if no rejection applies. |
| **Auto-Pay** | Not rejected and no hold: every line matched, quantities covered by goods received, prices within tolerance. | Default when every check passes. |

- **RE1.** Deterministic, testable code.
- **RE2.** During the demo the reviewer will run **invoices not in the test set** and expect outcomes that follow from these rules.
- **RE3. Human decisions.** An AP analyst may change the final status of **any** invoice. The system must record the original automatic outcome, the human decision, the identity of the person, the timestamp and the comment.
- **RE4.** The design must say how corrections to extracted data are handled on an invoice that already has a decision.

### Parameters (§7.1)

| Parameter | Meaning | Default |
|---|---|---|
| Price tolerance | Maximum difference between invoiced and PO unit price, per line | 2% |
| Analyst approval limit | Maximum invoice total an analyst may approve or override alone; above it, the AP Manager decides | 5,000 |

- **P1.** The defaults above are Contoso's current policy.
- **P2.** The AP Manager changes them **without a code change or a redeployment**.
- **P3.** Every change is recorded with the identity of the person and the timestamp.
- **P4.** Whether a change affects invoices that already have an automatic outcome is a design decision that must be made **and documented**.

## 7. Test data (§8)

- **T1.** AI-generated invoices (PDF and PNG). All reference suppliers and POs that exist in the reference data. The set includes clean matches and the variations Contoso's AP team sees in practice.
- **T2.** Inspect the test data before designing extraction and matching. Additional test data may be created.

## 8. Technical requirements (§9)

### Stack and model
- **S1.** Any technology; Next.js and Python are preferred.
- **S2.** The model is a GPT deployment in **Azure OpenAI** (v1 Responses API). Every call is made **server-side**.
- **S3.** The API key is confidential: never in client code or in a repository.
- **S4.** Rate limit of **10,000 tokens per minute**, about 3–4 extractions per minute; above it the API answers **HTTP 429**. The deployment is for building and demonstrating, not for load testing.

### Repository and documentation
- **S5.** Private GitHub repository, shared with the recruitment account.
- **S6.** The README is an architectural explanation: what was built, how the parts fit, why each decision was made, and known trade-offs and limitations. Written for a senior engineer joining on day two.
- **S7.** The README contains a **decision log**: every question raised or assumption made, the answer or assumption adopted, and its consequence for the implementation.

### Deployment (§9.4)
- **S8.** Deployment is optional but **carries significant weight**: two comparable solutions are separated by whether one runs as a deployed service. **Azure is preferred** and gets additional consideration.
- If deployed, it **must**:
  - **S9.** Be protected by authentication, with **role separation** between intake, analyst and manager.
  - **S10.** Keep the model API key and endpoint **only on the server side**. An unauthenticated deployment that exposes them is not acceptable.
  - **S11.** Have the README document **how it is deployed** and **how a change is released**.
- **S12.** The reviewers care about the choices made along the way: hosting, secrets management, cost. If not deployed, be ready to explain how it would be.

## 9. Evaluation criteria (§11)

### Technical
- Rule engine: precedence, edge cases, parameters.
- Extraction quality and robustness, especially line items.
- Correct matching between invoice, PO and GRNs.
- System design, data model, code structure.
- Error handling: model failures, rate limits, unreadable documents.
- Deployment as an authenticated cloud service (extra for Azure), and how it is documented.
- Security: role separation and protection of the model credentials.
- Completeness of the audit trail.
- Clarity of the documentation and the decision log.
- User experience for each role.

### Presentation (live demo)
- Explaining the architecture and design decisions clearly.
- Walking the reviewer through the system as if they were a client.
- Justifying trade-offs and assumptions.
- Answering questions, handling clarifications, and **reacting to a change request raised during the session**.
- Communicating limitations and possible improvements.
- Optional supporting material: diagrams, short slides, architecture overviews.

## 10. Ways of working (§12, §13)

- **W1.** AI coding tools are encouraged, but the candidate owns the code and must be able to explain every line.
- **W2.** The brief is intentionally underspecified. Gaps and ambiguities must be identified and clarified with the client, not guessed. Building in silence scores lower.
- **W3.** Questions about payment controls, tolerances and audit are answered from the Accounts Payable point of view; questions about POs, suppliers and deliveries from the Procurement point of view. Their answers may disagree, and reconciling them is part of the evaluation.

## Checklist for every plan or review

Before proposing or approving a change, check:

1. Which requirements above does it touch, and does it still meet all of them?
2. Does it contradict an entry in the decision log? If so, say so explicitly.
3. Does it keep the API key server-side and out of the repository (S3, S10)?
4. Does it keep role separation in the API, not only in the interface (S9, §3)?
5. Is every automatic and human action still in the audit trail (§4 step 7, RE3, P3)?
6. Does the README still explain it, and is any new assumption in the decision log (S6, S7)?