"""Built-in store catalog for purchase emails (best effort).

Each entry lists the sender domains a store's order, shipping and delivery
emails come from, and the subject words those emails use. A sender matches a
domain exactly or as a subdomain (``orders.oldnavy.com`` matches
``oldnavy.com``), so brand mail subdomains are covered without listing each.

The list is best effort: stores change their sending domains. To add a store,
append a ``StoreInfo`` line below; users can also add their own sender domains
in the options flow ("Custom sender domains").

Shared sending platforms are never matched on their own:

* **Narvar** (``narvar.com``, ``narvarnotify.com``) sends shipping emails for
  many brands from per-brand subdomains, so only those full subdomains are
  listed (``landsend.narvar.com``), never the bare platform domain.
* **Shopify** (``shopifyemail.com``) sends for thousands of shops from the same
  domain. A ``kind="shopify"`` entry matches only when the sender is on
  ``shopifyemail.com`` *and* either the shop id in the plus-address
  (``store+6723108924@t.shopifyemail.com``) is the shop's known id, or the
  shop's name appears as whole words in the From display name or the Subject.
  Shop ids are built in for some shops and learned on the first name match
  (v0.2.6). A shop whose id is known never matches mail with another id.

Pharmacy mail (Amazon Pharmacy) is never read, whatever is opted in (v0.2.6).
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


SHOPIFY_DOMAIN = "shopifyemail.com"
# Shared sending platforms: never usable as a sender domain on their own.
SHARED_DOMAINS = frozenset({"narvar.com", "narvarnotify.com", SHOPIFY_DOMAIN})


@dataclass(frozen=True, slots=True)
class StoreInfo:
    """One store: a stable key, a display name, sender domains, subject words."""

    key: str
    name: str
    domains: tuple[str, ...]
    keywords: tuple[str, ...] = ORDER_KEYWORDS
    uniform_supplier: bool = False
    kind: str = "domain"  # "domain" or "shopify" (matched by name, see above)
    shop_ids: tuple[str, ...] = ()  # Shopify plus-address ids (v0.2.6)


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
    StoreInfo("rei", "REI", ("rei.com",)),
    StoreInfo("costco", "Costco", ("costco.com",)),
    StoreInfo("etsy", "Etsy", ("etsy.com",)),
    StoreInfo("zappos", "Zappos", ("zappos.com",)),
    StoreInfo("dsw", "DSW", ("dsw.com",)),
    # Heather's stores (v0.2.2). Narvar entries are full per-brand subdomains.
    StoreInfo(
        "lands_end",
        "Lands' End",
        ("landsend.com", "landsend.narvar.com", "landsend.narvarnotify.com"),
    ),
    StoreInfo("american_tall", "American Tall", ("americantall.com",)),
    StoreInfo("llbean", "L.L.Bean", ("llbean.com",)),  # sends from e5.llbean.com
    StoreInfo(
        "duluth", "Duluth Trading", ("duluthtrading.com", "duluthtrading.narvar.com")
    ),
    StoreInfo("eddie_bauer", "Eddie Bauer", ("eddiebauer.com",)),
    StoreInfo(
        "outdoor_research",
        "Outdoor Research",
        ("outdoorresearch.com", "outdoorresearch.narvar.com"),
    ),
    StoreInfo(
        "backcountry", "Backcountry", ("backcountry.com", "backcountry.narvar.com")
    ),
    StoreInfo("columbia", "Columbia", ("columbia.com", "columbia1.narvar.com")),
    StoreInfo("carhartt", "Carhartt", ("carhartt.com",)),  # nat.carhartt.com
    StoreInfo(
        "coldwater_creek",
        "Coldwater Creek",
        ("coldwatercreek.com", "coldwatercreek.narvar.com"),
    ),
    StoreInfo("brooks_brothers", "Brooks Brothers", ("brooksbrothers.com",)),
    StoreInfo("orvis", "Orvis", ("orvis.com", "email-orvis.com")),
    StoreInfo("darn_tough", "Darn Tough", ("darntough.com",)),
    StoreInfo("woolx", "Woolx", ("woolx.com",)),
    StoreInfo("outway", "Outway", ("outway.com",)),
    StoreInfo("meundies", "MeUndies", ("meundies.com",)),  # info.meundies.com
    StoreInfo("underoutfit", "Underoutfit", ("underoutfit.com",)),
    StoreInfo("truekind", "Truekind", ("truekind.com",)),
    StoreInfo("cinderella_shoes", "Cinderella Shoes", ("cinderellashoes.com",)),
    # Tall women's apparel (v0.2.6). Orders come through Shopify with the
    # display name "Amalli Talli"; the subjects never name the store.
    StoreInfo(
        "amalli_talli",
        "Amalli Talli",
        (SHOPIFY_DOMAIN, "amallitalli.com"),
        kind="shopify",
        shop_ids=("6723108924",),
    ),
    # Uniform suppliers: garments from them start with "uniform" ticked.
    StoreInfo("blauer", "Blauer", ("blauer.com",), uniform_supplier=True),
    StoreInfo(
        "first_tactical",
        "First Tactical",
        (SHOPIFY_DOMAIN,),
        uniform_supplier=True,
        kind="shopify",
    ),
)
BY_KEY: dict[str, StoreInfo] = {store.key: store for store in CATALOG}

_DOMAIN = re.compile(
    r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)


_SHOP_ID = re.compile(r"^store\+(\d{4,20})$", re.IGNORECASE)


def shop_id(sender_local: str | None) -> str | None:
    """The Shopify shop id in a plus-address local part (``store+123`` → 123)."""
    match = _SHOP_ID.match((sender_local or "").strip())
    return match.group(1) if match else None


PHARMACY_DOMAINS = ("pharmacy.amazon.com",)


def is_pharmacy(
    sender_local: str | None, sender_domain: str | None, sender_name: str | None = ""
) -> bool:
    """Amazon Pharmacy mail: never read, stored or logged by name (v0.2.6)."""
    domain = (sender_domain or "").lower()
    local = (sender_local or "").lower()
    if any(domain_matches(domain, d) for d in PHARMACY_DOMAINS):
        return True
    if local.startswith("pharmacy") and domain_matches(domain, "amazon.com"):
        return True
    return name_in("Amazon Pharmacy", sender_name)


def is_uniform_supplier(store_key: str | None) -> bool:
    """True for catalog stores marked as uniform suppliers."""
    store = BY_KEY.get(store_key or "")
    return bool(store and store.uniform_supplier)


def normalize_domain(value: str) -> str | None:
    """Lower-case a sender domain (``@`` and a leading dot allowed); None if invalid."""
    text = value.strip().lower().lstrip("@.").rstrip(".")
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    return text if _DOMAIN.match(text) else None


def parse_domains(text: str) -> tuple[list[str], list[str]]:
    """Split a comma/space separated list into (valid domains, invalid entries).

    A shared sending platform (Narvar, Shopify) is invalid on its own: it would
    opt in to every brand that sends through it.
    """
    valid: list[str] = []
    invalid: list[str] = []
    for part in re.split(r"[,\s;]+", text or ""):
        if not part:
            continue
        domain = normalize_domain(part)
        if domain is None or domain in SHARED_DOMAINS:
            invalid.append(part)
        elif domain not in valid:
            valid.append(domain)
    return valid, invalid


def parse_names(text: str) -> tuple[list[str], list[str]]:
    """Comma-separated Shopify shop names: (valid names, invalid entries)."""
    valid: list[str] = []
    invalid: list[str] = []
    for part in (text or "").split(","):
        name = " ".join(part.split())
        if not name:
            continue
        if not 2 <= len(name) <= 60 or not re.search(r"\w", name):
            invalid.append(name)
        elif name.casefold() not in {v.casefold() for v in valid}:
            valid.append(name)
    return valid, invalid


def name_in(name: str, *texts: str | None) -> bool:
    """The name appears as whole words (case-insensitive) in any of the texts."""
    pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
    return any(text and pattern.search(text) for text in texts)


def domain_matches(sender: str, domain: str) -> bool:
    """Exact match or subdomain match (``a.b.com`` matches ``b.com``)."""
    sender = sender.lower().rstrip(".")
    return sender == domain or sender.endswith("." + domain)


@dataclass(frozen=True, slots=True)
class OptedIn:
    """What the user opted in to: catalog stores plus custom domains."""

    store_keys: tuple[str, ...]
    custom_domains: tuple[str, ...]
    shopify_names: tuple[str, ...] = ()
    learned_ids: tuple[tuple[str, str], ...] = ()  # (store key, shop id), v0.2.6

    def __post_init__(self) -> None:
        """Shared platforms are never a sender on their own, even from storage."""
        kept = tuple(d for d in self.custom_domains if d not in SHARED_DOMAINS)
        object.__setattr__(self, "custom_domains", kept)

    def stores(self) -> list[StoreInfo]:
        """Opted-in catalog stores (unknown keys ignored)."""
        return [BY_KEY[key] for key in self.store_keys if key in BY_KEY]

    def shopify(self) -> list[tuple[str, str]]:
        """(store key, shop name) for every opted-in Shopify shop."""
        out = [(s.key, s.name) for s in self.stores() if s.kind == "shopify"]
        out += [(f"shopify:{n.casefold()}", n) for n in self.shopify_names]
        return out

    def shop_ids(self) -> dict[str, set[str]]:
        """Known shop ids per opted-in Shopify store key (built in + learned)."""
        out: dict[str, set[str]] = {}
        keys = {key for key, _name in self.shopify()}
        for store in self.stores():
            if store.kind == "shopify" and store.shop_ids:
                out.setdefault(store.key, set()).update(store.shop_ids)
        for key, value in self.learned_ids:
            if key in keys:
                out.setdefault(key, set()).add(value)
        return out

    def count(self) -> int:
        """Stores + custom domains + Shopify names (for the status line)."""
        return len(self.stores()) + len(self.custom_domains) + len(self.shopify_names)

    def domains(self) -> list[str]:
        """Every opted-in sender domain (Shopify shops are searched by name)."""
        out: list[str] = []
        for store in self.stores():
            # A Shopify shop may also send from its own domain (Amalli Talli).
            own = [d for d in store.domains if d != SHOPIFY_DOMAIN]
            out.extend(d for d in own if d not in out)
        out.extend(d for d in self.custom_domains if d not in out)
        return out

    def keywords(self) -> list[str]:
        """Subject words used for the server-side search."""
        words: list[str] = []
        for store in self.stores() or [StoreInfo("", "", (), ORDER_KEYWORDS)]:
            words.extend(w for w in store.keywords if w not in words)
        if self.custom_domains or self.shopify_names:
            words.extend(w for w in ORDER_KEYWORDS if w not in words)
        return words

    def match(
        self,
        sender_domain: str | None,
        sender_name: str | None = None,
        subject: str | None = None,
        sender_local: str | None = None,
    ) -> tuple[str, str] | None:
        """(store key, store name) for an opted-in sender, else None.

        Shopify mail matches by the shop id in the plus-address, else by shop
        name in the display name or subject (unless that shop's id is known
        and differs). Any other Shopify mail is rejected like a non-opted
        sender, and pharmacy mail is never matched.
        """
        if not sender_domain or is_pharmacy(sender_local, sender_domain, sender_name):
            return None
        if domain_matches(sender_domain, SHOPIFY_DOMAIN):
            known = self.shop_ids()
            found = shop_id(sender_local)
            shops = self.shopify()
            if found:
                for key, name in shops:
                    if found in known.get(key, set()):
                        return key, name
            for key, name in shops:
                if found and known.get(key) and found not in known[key]:
                    continue  # another shop using this one's name
                if name_in(name, sender_name, subject):
                    return key, name
            return None
        for store in self.stores():
            own = [d for d in store.domains if d != SHOPIFY_DOMAIN]
            if any(domain_matches(sender_domain, d) for d in own):
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
