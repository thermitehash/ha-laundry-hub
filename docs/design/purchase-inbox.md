# Purchase inbox (built in v0.2.1)

Status: **built in v0.2.1.** This was the v0.2.0 design stub. The code lives in
`stores.py` (store catalog), `mailbox.py` (read-only IMAP), `extract.py` (AI
extraction and validation) and `purchases.py` (cards, matching, scheduling). It
differs from the stub as follows:

- Laundry Hub owns its IMAP connection (`aioimaplib`), never core IMAP. The
  folder is opened with `EXAMINE`, bodies are fetched with `BODY.PEEK`, and only
  the text/plain and text/html parts of opted-in senders are fetched (500 KB cap).
- Gmail filters on Google's side with `X-GM-RAW`; generic IMAP uses `SINCE` and
  `FROM` per domain, with the subject filter applied locally after headers.
- Cards are a `purchases` collection in the store (schema 3.2, a minor bump).
  Matching copies card photos onto the garment. It never moves them.
- Links the AI returns must appear verbatim in the email text that was sent.

## Flow

1. **Connect:** Laundry Hub's options flow gets its own IMAP connection (host,
   user, app password stored in the config entry) and an opt-in list of stores.
2. **Collect:** on a schedule, fetch new messages from allowlisted senders only
   (for example `orders@oldnavy.com`). Everything else is skipped unread.
3. **Extract:** for each matching order confirmation, send the message text to
   the configured AI Task entity with a fixed structure: item name, store, order
   date, price, currency, image URL, product URL, size, colour, order reference.
4. **Inbox:** each extracted item becomes a purchase card in the panel's Add flow
   ("From a recent order?").
5. **Match:** while enrolling (or from garment detail), the user picks the card
   that matches the garment. Its fields fill `garment.purchase` with
   `source: "email"`, and its image is imported through the same photo pipeline
   as a product-URL import.

## Garment data (already in v0.2.0)

`purchase = {store, purchased_on, price, currency, product_url, order_ref, source}`,
where `source` is `manual`, `url` or `email`.

## Privacy constraints

- **Sender allowlist:** only messages from senders the user has opted into are read.
- **Only matching emails go to AI:** no other message content leaves Home Assistant.
- **The email body is never stored.** Only the extracted fields are kept, and
  cards that are dismissed are deleted.
- IMAP credentials live in the config entry and are redacted in diagnostics.
- A clear note in the options flow says which provider receives order emails.
