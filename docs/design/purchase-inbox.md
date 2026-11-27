# Purchase inbox (design stub, built in v0.2.1)

Status: design only. v0.2.0 ships the data model (a nested `purchase` dict on
each garment) so v0.2.1 can attach email-derived purchases without another
storage migration.

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
