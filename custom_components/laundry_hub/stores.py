"""Built-in store catalog for purchase emails (best effort).

Each entry lists the sender domains a store's order, shipping and delivery
emails come from, and the subject words those emails use. A sender matches a
domain exactly or as a subdomain (``orders.oldnavy.com`` matches
``oldnavy.com``), so brand mail subdomains are covered without listing each.

The list is best effort: stores change their sending domains. To add a store,
append a ``StoreInfo`` line below; users can also add their own sender domains
in the options flow ("Custom sender domains").
"""

from __future__ import annotations

from dataclasses import dataclass
import re

ORDER_KEYWORDS: tuple[str, ...] = (
    "order",
    "ordered",
    "purchase",
    "receipt",
    "confirmation",
    "confirmed",
    "shipped",
    "shipping",
    "delivered",
    "on its way",
    "on the way",
)


@dataclass(frozen=True, slots=True)
class StoreInfo:
    """One store: a stable key, a display name, sender domains, subject words."""

    key: str
    name: str
    domains: tuple[str, ...]
    keywords: tuple[str, ...] = ORDER_KEYWORDS


CATALOG: tuple[StoreInfo, ...] = (
    StoreInfo("amazon", "Amazon", ("amazon.com",)),
    StoreInfo("target", "Target", ("target.com",)),
    StoreInfo("walmart", "Walmart", ("walmart.com",)),
    StoreInfo("kohls", "Kohl's", ("kohls.com",)),
    StoreInfo("old_navy", "Old Navy", ("oldnavy.com",)),
    StoreInfo("gap", "Gap", ("gap.com", "gapfactory.com")),
    StoreInfo("banana_republic", "Banana Republic", ("bananarepublic.com",)),
    StoreInfo("athleta", "Athleta", ("athleta.com",)),
    StoreInfo("nordstrom", "Nordstrom", ("nordstrom.com",)),
    StoreInfo("nordstrom_rack", "Nordstrom Rack", ("nordstromrack.com",)),
    StoreInfo("macys", "Macy's", ("macys.com",)),
    StoreInfo("jcpenney", "JCPenney", ("jcpenney.com", "jcp.com")),
    StoreInfo("hm", "H&M", ("hm.com",)),
    StoreInfo("zara", "Zara", ("zara.com",)),
    StoreInfo("uniqlo", "Uniqlo", ("uniqlo.com",)),
    StoreInfo("lululemon", "Lululemon", ("lululemon.com",)),
    StoreInfo("nike", "Nike", ("nike.com",)),
    StoreInfo("adidas", "Adidas", ("adidas.com",)),
    StoreInfo("american_eagle", "American Eagle / Aerie", ("ae.com", "aerie.com")),
    StoreInfo(
        "abercrombie",
        "Abercrombie / Hollister",
        ("abercrombie.com", "anf.com", "hollisterco.com"),
    ),
    StoreInfo("jcrew", "J.Crew", ("jcrew.com",)),
    StoreInfo("madewell", "Madewell", ("madewell.com",)),
    StoreInfo("torrid", "Torrid", ("torrid.com",)),
    StoreInfo("lane_bryant", "Lane Bryant", ("lanebryant.com",)),
    StoreInfo("thredup", "ThredUp", ("thredup.com",)),
    StoreInfo("poshmark", "Poshmark", ("poshmark.com",)),
    StoreInfo("shein", "Shein", ("shein.com", "sheinemail.com")),
    StoreInfo("quince", "Quince", ("onequince.com", "quince.com")),
    StoreInfo("universal_standard", "Universal Standard", ("universalstandard.com",)),
    StoreInfo("duluth", "Duluth Trading", ("duluthtrading.com",)),
    StoreInfo("rei", "REI", ("rei.com",)),
    StoreInfo("columbia", "Columbia", ("columbia.com",)),
    StoreInfo("llbean", "L.L.Bean", ("llbean.com",)),
    StoreInfo("costco", "Costco", ("costco.com",)),
    StoreInfo("etsy", "Etsy", ("etsy.com",)),
    StoreInfo("zappos", "Zappos", ("zappos.com",)),
    StoreInfo("dsw", "DSW", ("dsw.com",)),
)
BY_KEY: dict[str, StoreInfo] = {store.key: store for store in CATALOG}

_DOMAIN = re.compile(
    r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


def normalize_domain(value: str) -> str | None:
    """Lower-case a sender domain (``@`` and a leading dot allowed); None if invalid."""
    text = value.strip().lower().lstrip("@.").rstrip(".")
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    return text if _DOMAIN.match(text) else None


def parse_domains(text: str) -> tuple[list[str], list[str]]:
    """Split a comma/space separated list into (valid domains, invalid entries)."""
    valid: list[str] = []
    invalid: list[str] = []
    for part in re.split(r"[,\s;]+", text or ""):
        if not part:
            continue
        domain = normalize_domain(part)
        if domain is None:
            invalid.append(part)
        elif domain not in valid:
            valid.append(domain)
    return valid, invalid


def domain_matches(sender: str, domain: str) -> bool:
    """Exact match or subdomain match (``a.b.com`` matches ``b.com``)."""
    sender = sender.lower().rstrip(".")
    return sender == domain or sender.endswith("." + domain)


@dataclass(frozen=True, slots=True)
class OptedIn:
    """What the user opted in to: catalog stores plus custom domains."""

    store_keys: tuple[str, ...]
    custom_domains: tuple[str, ...]

    def stores(self) -> list[StoreInfo]:
        """Opted-in catalog stores (unknown keys ignored)."""
        return [BY_KEY[key] for key in self.store_keys if key in BY_KEY]

    def domains(self) -> list[str]:
        """Every opted-in sender domain."""
        out: list[str] = []
        for store in self.stores():
            out.extend(d for d in store.domains if d not in out)
        out.extend(d for d in self.custom_domains if d not in out)
        return out

    def keywords(self) -> list[str]:
        """Subject words used for the server-side search."""
        words: list[str] = []
        for store in self.stores() or [StoreInfo("", "", (), ORDER_KEYWORDS)]:
            words.extend(w for w in store.keywords if w not in words)
        if self.custom_domains:
            words.extend(w for w in ORDER_KEYWORDS if w not in words)
        return words

    def match(self, sender_domain: str | None) -> tuple[str, str] | None:
        """(store key, store name) for an opted-in sender, else None."""
        if not sender_domain:
            return None
        for store in self.stores():
            if any(domain_matches(sender_domain, d) for d in store.domains):
                return store.key, store.name
        for domain in self.custom_domains:
            if domain_matches(sender_domain, domain):
                return f"custom:{domain}", _display(domain)
        return None


def _display(domain: str) -> str:
    """A readable store name for a custom domain (``shop.example.com`` → Example)."""
    parts = domain.split(".")
    core = parts[-2] if len(parts) >= 2 else domain
    return core[:1].upper() + core[1:]
