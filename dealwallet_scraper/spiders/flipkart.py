import asyncio
from concurrent.futures import ProcessPoolExecutor
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin

from crawl4ai import AsyncWebCrawler
import scrapy
from datetime import datetime, timezone, timedelta

# ============================================================
# CONFIGURATION
# ============================================================
BASE_URLS = {
    "Nutrition Supplements": (
        "https://www.flipkart.com/search?"
        "q=nutrition+supplements"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=nutrition+supplements



    "Health & Beauty": (
        "https://www.flipkart.com/search?"
        "q=health+beauty"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=health+beauty


    "Mobile": (
        "https://www.flipkart.com/search?"
        "q=mobile"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=mobile


    "Medical Pharmacy": (
        "https://www.flipkart.com/search?"
        "q=medical+pharmacy"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=medical+pharmacy


    "Grocery": (
        "https://www.flipkart.com/search?"
        "q=grocery"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=grocery


    "Laptops": (
        "https://www.flipkart.com/search?"
        "q=laptops"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=laptops


    "Home & Kitchen": (
        "https://www.flipkart.com/search?"
        "q=home+kitchen"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=home+kitchen


    "Sports": (
        "https://www.flipkart.com/search?"
        "q=sports"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=sports


    "Beauty": (
        "https://www.flipkart.com/search?"
        "q=beauty"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=beauty


    "Others": (
        "https://www.flipkart.com/search?"
        "q=others"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=others


    "Bags": (
        "https://www.flipkart.com/search?"
        "q=bags"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=bags


    "Shirts": (
        "https://www.flipkart.com/search?"
        "q=shirts"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=shirts


    "Flowers & Gifts": (
        "https://www.flipkart.com/search?"
        "q=flowers+gifts"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=flowers+gifts


    "Home Decor": (
        "https://www.flipkart.com/search?"
        "q=home+decor"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=home+decor


    "Gaming": (
        "https://www.flipkart.com/search?"
        "q=gaming"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=gaming


    "Computers Gadgets": (
        "https://www.flipkart.com/search?"
        "q=computers+gadgets"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=computers+gadgets


    "Home Appliances": (
        "https://www.flipkart.com/search?"
        "q=home+appliances"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=home+appliances


    "Food": (
        "https://www.flipkart.com/search?"
        "q=food"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=food


    "Baby & Kids": (
        "https://www.flipkart.com/search?"
        "q=baby+kids"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=baby+kids


    "Ayurveda": (
        "https://www.flipkart.com/search?"
        "q=ayurveda"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=ayurveda


    "Seasonal Wear": (
        "https://www.flipkart.com/search?"
        "q=seasonal+wear"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=seasonal+wear


    "Mobile Accessories": (
        "https://www.flipkart.com/search?"
        "q=mobile+accessories"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=mobile+accessories


    "Travel": (
        "https://www.flipkart.com/search?"
        "q=travel"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=travel


    "Electronics": (
        "https://www.flipkart.com/search?"
        "q=electronics"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=electronics


    "Pet Essentials": (
        "https://www.flipkart.com/search?"
        "q=pet+essentials"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=pet+essentials


    "Travel,Hotels & Transport": (
        "https://www.flipkart.com/search?"
        "q=travel+hotels+transport"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=travel+hotels+transport


    "Fashion & Lifestyle": (
        "https://www.flipkart.com/search?"
        "q=fashion+lifestyle"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=fashion+lifestyle


    "Furnitures": (
        "https://www.flipkart.com/search?"
        "q=furnitures"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=furnitures


    "Makeup": (
        "https://www.flipkart.com/search?"
        "q=makeup"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=makeup


    "Gym Equipments": (
        "https://www.flipkart.com/search?"
        "q=gym+equipments"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=gym+equipments


    "Shoes": (
        "https://www.flipkart.com/search?"
        "q=shoes"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=shoes


    "Toys Games": (
        "https://www.flipkart.com/search?"
        "q=toys+games"
        "&otracker=search"
        "&otracker1=search"
        "&marketplace=FLIPKART"
        "&as-show=on"
        "&as=off"
        "&as-pos=1"
        "&as-type=HISTORY"
        "&page="
    ),
    # Verification URL:
    # https://www.flipkart.com/search?q=toys+games
}


MAX_PAGES = 5
MAX_PRODUCTS_PER_CATEGORY = 100


# ============================================================
# DATA TYPE HELPERS
# ============================================================

def extract_int(value):
    """
    Convert text such as:

        ₹24,999
        24,999
        16% OFF

    into an integer.

    Returns None when conversion is not possible.
    """

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    digits = "".join(
        character
        for character in value
        if character.isdigit()
    )

    if not digits:
        return None

    try:

        return int(digits)

    except (TypeError, ValueError):

        return None


def extract_rating(value):
    """
    Convert rating text such as:

        4.5
        4.5 ★
        4

    into float.

    Returns None when conversion is not possible.
    """

    if value is None:
        return None

    value = str(value).strip()

    if not value:
        return None

    try:

        cleaned = "".join(
            character
            for character in value
            if character.isdigit() or character == "."
        )

        if not cleaned:
            return None

        return float(cleaned)

    except (TypeError, ValueError):

        return None


# ============================================================
# FLIPKART LISTING SELECTOR HELPERS
# ============================================================

def first_selected(element, selectors):
    """Return the first matching BeautifulSoup element."""
    for selector in selectors:
        found = element.select_one(selector)
        if found:
            return found
    return None


def get_element_text(element, selectors):
    """Return clean text from the first matching selector."""
    found = first_selected(element, selectors)

    if not found:
        return ""

    return found.get_text(" ", strip=True)


def get_product_url(element):
    """Extract a product URL using multiple Flipkart listing layouts."""
    link = first_selected(
        element,
        [
            "a.k7wcnx[href]",
            "a.GnxRXv[href*='/p/']",
            "a.pIpigb[href*='/p/']",
            "a.fb4uj3[href*='/p/']",
        ],
    )

    if not link:
        return ""

    href = link.get("href")

    if not href:
        return ""

    return urljoin(
        "https://www.flipkart.com",
        href,
    )


def extract_listing_description(product):
    """
    Extract description/specifications available directly
    on the Flipkart listing card.

    Different Flipkart categories use different listing
    layouts, so multiple selectors are supported.
    """

    # Layout used by categories such as Mobile.
    feature_elements = product.select(
        "li.DTBslk"
    )

    features = []

    for feature in feature_elements:
        feature_text = feature.get_text(
            " ",
            strip=True,
        )

        if feature_text:
            features.append(feature_text)

    if features:
        return ", ".join(features)

    # Do not use broad font-based selectors here.
    # They also match unrelated Flipkart UI text.
    return ""


def canonical_flipkart_product_url(product_url):
    """
    Flipkart listing URLs contain many search/session/tracking parameters.
    For the product-detail request, keep only the canonical /p/<id> path
    and the pid parameter. This avoids carrying listing/search state into
    the detail-page request.
    """
    if not product_url:
        return product_url

    try:
        from urllib.parse import urlsplit, parse_qs, urlencode

        parts = urlsplit(product_url)

        # Only canonicalize actual Flipkart product pages.
        if "/p/" not in parts.path:
            return product_url

        query = parse_qs(parts.query)

        params = {}

        pid = query.get("pid")
        if pid:
            params["pid"] = pid[0]

        canonical = (
            f"{parts.scheme or 'https'}://"
            f"{parts.netloc or 'www.flipkart.com'}"
            f"{parts.path}"
        )

        if params:
            canonical += "?" + urlencode(params)

        return canonical

    except Exception:
        return product_url


async def extract_detail_description(crawler, product_url):
    """
    Extract product description from a Flipkart product page.

    Extraction order:
    1. Actual product description text.
    2. Raw-HTML actual description fallback.
    3. Flipkart specification/highlight grid only as a legacy fallback.

    Important:
    - Flipkart can keep the specification section inside a hidden DOM
      container. We intentionally parse the complete returned HTML and do
      NOT ignore elements just because an ancestor has the `hidden` attribute.
    - The current Flipkart structure uses `grid-column-1` and
      `grid-formation-dynamic` for these fields. We therefore do not depend
      on a literal "Product highlights" heading or `grid-column-2`.
    - Each specification field is paired inside its own
      `grid-formation-dynamic` block so nearby variant controls are not
      accidentally treated as product data.
    """
    if not product_url:
        return None, "Not Found"

    try:
        original_product_url = product_url
        canonical_url = canonical_flipkart_product_url(product_url)

        print("\n[DETAIL] Opening product page:")
        print(canonical_url)

        if canonical_url != original_product_url:
            print("[DETAIL] Canonical URL applied:")
            print("[DETAIL] Original:", original_product_url)
            print("[DETAIL] Canonical:", canonical_url)

        # The important data is already present in the returned HTML for the
        # supplied Flipkart page. Scrolling is still useful for lazy-loaded
        # content, but we no longer try to click a "Product highlights"
        # heading because that heading is absent on some current pages.
        js_code = [
            """
            (() => {
                const sleep = (ms) =>
                    new Promise(resolve => setTimeout(resolve, ms));

                async function preparePage() {
                    window.scrollTo(0, 0);
                    await sleep(800);

                    window.scrollTo(0, document.body.scrollHeight);
                    await sleep(1800);

                    window.scrollTo(0, 0);
                    await sleep(800);
                }

                return preparePage();
            })();
            """,
        ]

        detail_result = await crawler.arun(
            url=canonical_url,
            wait_for="css:body",
            delay_before_return_html=4.0,
            js_code=js_code,
        )

        html = getattr(detail_result, "html", None)

        if not html:
            print("[DETAIL DEBUG] No HTML returned.")
            print(
                "[DETAIL DEBUG] Success:",
                getattr(detail_result, "success", None),
            )
            print(
                "[DETAIL DEBUG] Status:",
                getattr(detail_result, "status_code", None),
            )
            return None, "Not Found"

        detail_soup = BeautifulSoup(html, "html.parser")

        # ------------------------------------------------------------
        # Shared helpers
        # ------------------------------------------------------------
        variant_labels = {
            "vanilla",
            "kulfi",
            "plain",
            "chocolate",
            "orange",
            "tulsi",
            "unflavoured",
            "unflavour",
        }

        ignored_labels = {
            "product highlights",
            "show more",
            "show less",
        }

        def element_text(element):
            if element is None:
                return ""

            return clean_text(
                element.get_text(" ", strip=True)
            )

        def is_inside_spec_grid(element):
            """Return True when an element belongs to a Flipkart spec grid."""
            current = element.parent

            for _ in range(8):
                if current is None:
                    break

                classes = current.get("class", [])
                if "grid-formation-dynamic" in classes:
                    return True

                if "grid-formation" in classes:
                    return True

                current = current.parent

            return False

        # ------------------------------------------------------------
        # 1. ACTUAL PRODUCT DESCRIPTION
        # ------------------------------------------------------------
        # The supplied HTML shows the actual description as a standalone
        # v1zwn21n/v1zwn27 element that is NOT inside a grid-formation block.
        # This prevents fields such as Brand, Quantity and Composition from
        # being incorrectly returned as the main description.
        description_candidates = []

        for element in detail_soup.select(
            "div.v1zwn21n.v1zwn27"
        ):
            value = element_text(element)

            if len(value) < 40:
                continue

            if is_inside_spec_grid(element):
                continue

            lowered = value.lower()

            if lowered in ignored_labels:
                continue

            blocked_exact = {
                "add to cart",
                "buy now",
                "delivery",
                "ratings & reviews",
                "ratings and reviews",
                "legal disclaimer",
            }

            if lowered in blocked_exact:
                continue

            # Do not use the generic Flipkart legal disclaimer as a product
            # description if it is present in the same text structure.
            if lowered.startswith(
                "flipkart endeavours to ensure that the sellers"
            ):
                continue

            description_candidates.append(value)

        if description_candidates:
            # Prefer the longest standalone description block. This matches
            # the actual product-description block in the supplied HTML and
            # avoids short incidental text nodes.
            description = max(
                description_candidates,
                key=len,
            )

            print(
                "[DETAIL] Actual description extracted | "
                "Source: Product Detail"
            )
            print(
                "[DETAIL] Description length:",
                len(description),
            )

            return description, "Product Detail"

        # ------------------------------------------------------------
        # 1B. RAW HTML ACTUAL DESCRIPTION FALLBACK
        # ------------------------------------------------------------
        # Crawl4AI sometimes returns the correct Flipkart HTML, but
        # BeautifulSoup does not expose the React-generated description
        # element through the CSS selector tree. The supplied HTML shows
        # the real description as a leaf div using these classes:
        #
        #   v1zwn21n v1zwn27
        #
        # and it is outside the grid-formation specification blocks.
        # Use a leaf-text regex here only as a fallback. We deliberately
        # require the element body to contain text without another HTML tag
        # so we do not accidentally capture an entire specification block.
        raw_description_candidates = []

        raw_description_pattern = re.compile(
            r"<div[^>]*class=[\"'] [^\"']*\bv1zwn21n\b[^\"']*\bv1zwn27\b[^\"']*[\"'][^>]*>"
            r"([^<]{40,})"
            r"</div>",
            re.I | re.S | re.X,
        )

        for match in raw_description_pattern.finditer(html):
            value = clean_text(
                BeautifulSoup(
                    match.group(1),
                    "html.parser",
                ).get_text(" ", strip=True)
            )

            if len(value) < 40:
                continue

            lowered = value.lower()

            if lowered in ignored_labels:
                continue

            if lowered.startswith(
                "flipkart endeavours to ensure that the sellers"
            ):
                continue

            # Specification values are generally short. Keep longer
            # standalone text as the actual product description.
            raw_description_candidates.append(value)

        if raw_description_candidates:
            description = max(
                raw_description_candidates,
                key=len,
            )

            print(
                "[DETAIL] Raw HTML actual description extracted | "
                "Source: Product Detail"
            )
            print(
                "[DETAIL] Raw description length:",
                len(description),
            )

            return description, "Product Detail"

        # ------------------------------------------------------------
        # 2. CURRENT FLIPKART SPECIFICATION STRUCTURE
        # ------------------------------------------------------------
        # Current HTML observed from the supplied Flipkart page:
        #
        # grid-formation grid-column-1
        #   grid-formation-dynamic
        #       v1zwn21o v1zwn28  -> label
        #       v1zwn21n v1zwn27  -> value
        #
        # The entire block can be under <div hidden="">. BeautifulSoup
        # still parses it, so we intentionally include it.
        spec_blocks = detail_soup.select(
            "div.grid-formation-dynamic"
        )

        pairs = []
        seen_labels = set()

        # ------------------------------------------------------------
        # RAW-HTML FALLBACK
        # ------------------------------------------------------------
        # Crawl4AI can return Flipkart's React HTML with the expected
        # class names visible in the raw HTML, while BeautifulSoup's
        # selector tree may expose zero matching nodes. The logs show:
        #
        #   Product Highlights marker present: True
        #   grid-formation-dynamic blocks: 0
        #
        # Therefore, when the DOM selectors fail, parse the same
        # class names directly from the returned raw HTML.
        def raw_text(fragment):
            if not fragment:
                return ""

            fragment = re.sub(
                r"<script[^>]*>.*?</script>",
                " ",
                fragment,
                flags=re.I | re.S,
            )

            fragment = re.sub(
                r"<style[^>]*>.*?</style>",
                " ",
                fragment,
                flags=re.I | re.S,
            )

            return clean_text(
                BeautifulSoup(
                    fragment,
                    "html.parser",
                ).get_text(" ", strip=True)
            )

        def raw_class_nodes(*class_tokens):
            token_pattern = "".join(
                rf"(?=[^\"']*\b{re.escape(token)}\b)"
                for token in class_tokens
            )

            pattern = re.compile(
                rf"<(?:div|span|p)[^>]*class=[\"'][^\"']*"
                rf"{token_pattern}"
                rf"[^\"']*[\"'][^>]*>(.*?)</(?:div|span|p)>",
                re.I | re.S,
            )

            return [
                (match.start(), raw_text(match.group(1)))
                for match in pattern.finditer(html)
            ]

        if not pairs:
            raw_labels = raw_class_nodes(
                "v1zwn21o",
                "v1zwn28",
            )

            raw_values = raw_class_nodes(
                "v1zwn21n",
                "v1zwn27",
            )

            # If the exact class combination is not present because
            # Flipkart changed one of the class names, use the stable
            # v1zwn21o / v1zwn21n tokens as a secondary fallback.
            if not raw_labels:
                raw_labels = raw_class_nodes("v1zwn21o")

            if not raw_values:
                raw_values = raw_class_nodes("v1zwn21n")

            for label_position, label in raw_labels:
                label = clean_text(label)
                label_key = label.lower().strip()

                if not label_key:
                    continue

                if label_key in ignored_labels:
                    continue

                if label_key in variant_labels:
                    continue

                # Find the next label so that the value is taken only
                # from the label -> value interval.
                next_label_position = None

                for other_position, _ in raw_labels:
                    if other_position > label_position:
                        next_label_position = other_position
                        break

                value = ""

                for value_position, candidate in raw_values:
                    if value_position <= label_position:
                        continue

                    if (
                        next_label_position is not None
                        and value_position >= next_label_position
                    ):
                        break

                    candidate = clean_text(candidate)

                    if not candidate:
                        continue

                    if candidate.lower() in ignored_labels:
                        continue

                    value = candidate
                    break

                if not value:
                    continue

                if label_key == value.lower():
                    continue

                if label_key in seen_labels:
                    continue

                seen_labels.add(label_key)
                pairs.append(
                    f"{label}: {value}"
                )

            if pairs:
                print(
                    "[DETAIL] Raw HTML specification fallback extracted:",
                    len(pairs),
                    "fields",
                )

        for block in spec_blocks:
            label_nodes = block.select(
                "div.v1zwn21o.v1zwn28"
            )

            if not label_nodes:
                continue

            label = element_text(label_nodes[0])

            if not label:
                continue

            label_key = label.strip().lower()

            if label_key in ignored_labels:
                continue

            # Variant selectors are frequently rendered in similar nearby
            # DOM structures. They are not product specification fields.
            if label_key in variant_labels:
                continue

            value_nodes = block.select(
                "div.v1zwn21n.v1zwn27"
            )

            if not value_nodes:
                continue

            value = ""

            # Usually the first v1zwn21n.v1zwn27 node is the value. Skip any
            # nested/empty duplicate nodes and choose the first meaningful
            # one.
            for value_node in value_nodes:
                candidate = element_text(value_node)

                if not candidate:
                    continue

                if candidate.lower() in ignored_labels:
                    continue

                value = candidate
                break

            if not value:
                continue

            if label_key == value.lower():
                continue

            if label_key in seen_labels:
                # Keep the first occurrence from the current specification
                # structure. This avoids duplicate fields caused by React
                # responsive/hidden copies of the same section.
                continue

            seen_labels.add(label_key)
            pairs.append(f"{label}: {value}")

        # ------------------------------------------------------------
        # 3. DEBUG INFORMATION
        # ------------------------------------------------------------
        raw_html_lower = html.lower()
        marker_present = "product highlights" in raw_html_lower
        grid_column_1_count = len(
            detail_soup.select(
                "div.grid-formation.grid-column-1"
            )
        )
        grid_dynamic_count = len(spec_blocks)

        print("\n[DETAIL DEBUG]")
        print("-" * 70)
        print("URL:", canonical_url)
        print("HTML length:", len(html))
        print(
            "Product Highlights marker present:",
            marker_present,
        )
        print(
            "grid-column-1 blocks:",
            grid_column_1_count,
        )
        print(
            "grid-formation-dynamic blocks:",
            grid_dynamic_count,
        )
        print(
            "Specification pairs:",
            len(pairs),
        )
        print("-" * 70)

        if pairs:
            description = ", ".join(pairs)

            print(
                "[DETAIL] Flipkart specifications extracted:",
                len(pairs),
                "fields",
            )
            print(
                "[DETAIL] Description length:",
                len(description),
            )

            return description, "Product Highlights"

        # ------------------------------------------------------------
        # 4. FALLBACK: OTHER KNOWN DESCRIPTION SELECTORS
        # ------------------------------------------------------------
        fallback_selectors = [
            "div._1mXcCf",
            "div._1AN87M",
            "div[class*='description']",
        ]

        for selector in fallback_selectors:
            for element in detail_soup.select(selector):
                value = element_text(element)

                if len(value) < 40:
                    continue

                lowered = value.lower()

                if lowered.startswith(
                    "flipkart endeavours to ensure that the sellers"
                ):
                    continue

                if is_inside_spec_grid(element):
                    continue

                print(
                    "[DETAIL] Fallback description extracted | "
                    f"Selector: {selector}"
                )
                print(
                    "[DETAIL] Description length:",
                    len(value),
                )

                return value, "Product Detail"

        # ------------------------------------------------------------
        # 5. OPTIONAL SECOND FETCH
        # ------------------------------------------------------------
        # Only retry when neither the description nor the specification
        # structure was present. A retry is intentionally not performed just
        # because the literal words "Product highlights" are absent, because
        # the supplied current Flipkart HTML does not use that heading.
        if grid_dynamic_count == 0 and not description_candidates:
            print(
                "[DETAIL] No description/specification structure found. "
                "Retrying detail page once..."
            )

            retry_result = await crawler.arun(
                url=canonical_url,
                wait_for="css:body",
                delay_before_return_html=7.0,
                js_code=[
                    """
                    (() => {
                        const sleep = (ms) =>
                            new Promise(resolve => setTimeout(resolve, ms));

                        async function run() {
                            window.scrollTo(0, 0);
                            await sleep(1000);
                            window.scrollTo(0, document.body.scrollHeight);
                            await sleep(2500);
                            window.scrollTo(0, 0);
                            await sleep(1500);
                        }

                        return run();
                    })();
                    """
                ],
            )

            retry_html = getattr(retry_result, "html", None)

            if retry_html:
                retry_soup = BeautifulSoup(
                    retry_html,
                    "html.parser",
                )

                # Retry actual description.
                retry_candidates = []

                for element in retry_soup.select(
                    "div.v1zwn21n.v1zwn27"
                ):
                    value = element_text(element)

                    if len(value) < 40:
                        continue

                    if is_inside_spec_grid(element):
                        continue

                    lowered = value.lower()

                    if lowered.startswith(
                        "flipkart endeavours to ensure that the sellers"
                    ):
                        continue

                    retry_candidates.append(value)

                if retry_candidates:
                    retry_description = max(
                        retry_candidates,
                        key=len,
                    )

                    print(
                        "[DETAIL] Retry actual description extracted."
                    )
                    return retry_description, "Product Detail"

                # Retry specification grid.
                retry_pairs = []
                retry_seen = set()

                for block in retry_soup.select(
                    "div.grid-formation-dynamic"
                ):
                    labels = block.select(
                        "div.v1zwn21o.v1zwn28"
                    )
                    values = block.select(
                        "div.v1zwn21n.v1zwn27"
                    )

                    if not labels or not values:
                        continue

                    label = element_text(labels[0])
                    label_key = label.lower().strip()

                    if not label_key:
                        continue

                    if label_key in ignored_labels:
                        continue

                    if label_key in variant_labels:
                        continue

                    value = ""

                    for node in values:
                        candidate = element_text(node)
                        if candidate:
                            value = candidate
                            break

                    if not value:
                        continue

                    if label_key == value.lower():
                        continue

                    if label_key in retry_seen:
                        continue

                    retry_seen.add(label_key)
                    retry_pairs.append(
                        f"{label}: {value}"
                    )

                if retry_pairs:
                    description = ", ".join(retry_pairs)

                    print(
                        "[DETAIL] Flipkart specifications extracted "
                        "on retry:",
                        len(retry_pairs),
                        "fields",
                    )

                    return description, "Product Highlights"

        # ------------------------------------------------------------
        # 6. FINAL DIAGNOSTIC
        # ------------------------------------------------------------
        print(
            "[DETAIL] No description or Flipkart specification data "
            "found in returned Crawl4AI HTML."
        )

        if "product highlights" in raw_html_lower:
            marker_index = raw_html_lower.find(
                "product highlights"
            )

            snippet_start = max(0, marker_index - 500)
            snippet_end = min(
                len(html),
                marker_index + 1800,
            )

            snippet = clean_text(
                BeautifulSoup(
                    html[snippet_start:snippet_end],
                    "html.parser",
                ).get_text(" ", strip=True)
            )

            print("[DETAIL DEBUG] Highlight snippet:")
            print(snippet[:2200])
        else:
            print(
                "[DETAIL DEBUG] Current Flipkart page did not contain "
                "the literal 'Product highlights' marker."
            )

        return None, "Not Found"

    except Exception as exc:
        print(
            "[DETAIL] Exception while extracting:",
            product_url,
        )
        print("[DETAIL] Error:", repr(exc))
        return None, "Not Found"

def clean_text(value):
    if value is None:
        return ""

    text = str(value)
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

async def scrape_flipkart_async():

    products = []

    async with AsyncWebCrawler(
        verbose=True
    ) as crawler:

        # ====================================================
        # CATEGORY LOOP
        # ====================================================

        for category, base_url in BASE_URLS.items():

            print("\n" + "=" * 80)
            print(
                f"Starting Flipkart Category: "
                f"{category}"
            )
            print("=" * 80)

            # =================================================
            # PER-CATEGORY PRODUCT LIMIT
            # =================================================

            category_product_count = 0

            print(
                f"[INFO] Maximum products for this category: "
                f"{MAX_PRODUCTS_PER_CATEGORY}"
            )

            # =================================================
            # PAGE LOOP
            # =================================================

            for page in range(
                1,
                MAX_PAGES + 1
            ):

                # Stop before requesting another page when
                # this category has already reached the limit.
                if category_product_count >= MAX_PRODUCTS_PER_CATEGORY:
                    print(
                        f"[INFO] Category '{category}' reached "
                        f"{MAX_PRODUCTS_PER_CATEGORY} products. "
                        f"Stopping further page requests."
                    )
                    break

                url = f"{base_url}{page}"

                print("\n" + "-" * 80)
                print(
                    f"Category : {category}"
                )
                print(
                    f"Page     : {page}"
                )
                print(
                    f"URL      : {url}"
                )
                print("-" * 80)

                # =============================================
                # CRAWL PAGE
                # =============================================

                try:

                    result = await crawler.arun(
                        url=url
                    )

                except Exception as e:

                    print(
                        f"[ERROR] Crawl4AI failed "
                        f"on category={category}, "
                        f"page={page}: {e}"
                    )

                    continue

                if not result.success:

                    print(
                        f"[ERROR] Failed to scrape "
                        f"category={category}, "
                        f"page={page}"
                    )

                    continue

                # =============================================
                # PARSE HTML
                # =============================================

                soup = BeautifulSoup(
                    result.html,
                    "html.parser"
                )

                product_divs = soup.select(
                    "div[data-id]"
                )

                print(
                    f"[INFO] Product cards found: "
                    f"{len(product_divs)}"
                )

                page_count = 0

                # =============================================
                # PRODUCT LOOP
                # =============================================

                for product in product_divs:

                    try:

                        # =====================================
                        # NAME
                        # =====================================

                        name = get_element_text(
                            product,
                            [
                                "div.RG5Slk",
                                "a.pIpigb[title]",
                                "a.pIpigb",
                            ],
                        )


                        # =====================================
                        # PRODUCT LINK
                        # =====================================

                        product_url = get_product_url(
                            product
                        )


                        # =====================================
                        # IMAGE
                        # =====================================

                        image_element = first_selected(
                            product,
                            [
                                "img.UCc1lI[src]",
                                "img[src]",
                            ],
                        )

                        image_link = (
                            image_element.get(
                                "src",
                                ""
                            )
                            if image_element
                            else ""
                        )


                        # =====================================
                        # RATING
                        # =====================================

                        rating_text = get_element_text(
                            product,
                            [
                                "div.MKiFS6",
                            ],
                        )

                        rating = extract_rating(
                            rating_text
                        )


                        # =====================================
                        # PRICE
                        # =====================================

                        price_text = get_element_text(
                            product,
                            [
                                "div.hZ3P6w.DeU9vF",
                                "div.hZ3P6w",
                            ],
                        )

                        price = extract_int(
                            price_text
                        )


                        # =====================================
                        # ORIGINAL PRICE
                        # =====================================

                        original_price_text = get_element_text(
                            product,
                            [
                                "div.kRYCnD.gxR4EY",
                                "div.kRYCnD",
                            ],
                        )

                        original_price = extract_int(
                            original_price_text
                        )


                        # =====================================
                        # DISCOUNT
                        # =====================================

                        discount_text = get_element_text(
                            product,
                            [
                                "div.HQe8jr span",
                                "div.HQe8jr",
                            ],
                        )

                        discount = extract_int(
                            discount_text
                        )


                        # =====================================
                        # DESCRIPTION
                        # =====================================

                        description_source = "Not Found"

                        description = (
                            extract_listing_description(
                                product
                            )
                        )

                        if description:
                            description_source = "Listing"

                        # If the listing card does not expose
                        # description/specifications, fetch the
                        # product detail page.
                        if not description and product_url:
                            description, detail_source = (
                                await extract_detail_description(
                                    crawler,
                                    product_url,
                                )
                            )

                            if description:
                                description_source = detail_source


                        # =====================================
                        # DEBUG FIRST PRODUCT
                        # =====================================

                        if page_count == 0:

                            print(
                                "\n[DEBUG] FIRST PRODUCT"
                            )

                            print(
                                "-" * 60
                            )

                            print(
                                "Category      :",
                                category
                            )

                            print(
                                "Name          :",
                                name
                            )

                            print(
                                "Price         :",
                                price,
                                type(price).__name__
                            )

                            print(
                                "Original Price:",
                                original_price,
                                type(
                                    original_price
                                ).__name__
                            )

                            print(
                                "Discount      :",
                                discount,
                                type(
                                    discount
                                ).__name__
                            )

                            print(
                                "Rating        :",
                                rating,
                                type(
                                    rating
                                ).__name__
                            )

                            print(
                                "Currency      :",
                                "₹",
                                type("₹").__name__
                            )

                            print(
                                "Description   :",
                                description
                            )

                            print(
                                "Description Source:",
                                description_source
                            )

                            print(
                                "Image         :",
                                image_link
                            )

                            print(
                                "Product Link  :",
                                product_url
                            )

                            print(
                                "-" * 60
                            )


                        # =====================================
                        # REQUIRED FIELD CHECK
                        # =====================================

                        missing = []

                        if not name:

                            missing.append(
                                "name"
                            )

                        if price is None:

                            missing.append(
                                "price"
                            )

                        if rating is None:

                            missing.append(
                                "rating"
                            )

                        if discount is None:

                            missing.append(
                                "discount"
                            )

                        if not product_url:

                            missing.append(
                                "product_link"
                            )

                        if missing:

                            print(
                                f"[SKIP] "
                                f"{name or 'Unknown'} "
                                f"-> Missing: "
                                f"{', '.join(missing)}"
                            )

                            continue
                        # =========================================
                        # TIMESTAMP
                        # =========================================
                        
                        timestamp = datetime.now(
                            timezone(
                                timedelta(
                                    hours=5,
                                    minutes=30
                                )
                            )
                        ).strftime(
                            "%Y-%m-%dT%H:%M:%S"
                        )


                        # =====================================
                        # PRODUCT DATA
                        # =====================================
                        description = re.sub(r"^/:\s*", "", description).strip()

                        product_data = {

                            "name": name,

                            "price": price,

                            "original_price": (
                                original_price
                                if original_price
                                is not None
                                else None
                            ),

                            "currency": "₹",

                            "discount": discount,

                            "ratings": rating,

                            "description": (
                                description
                                if description
                                else "N/A"
                            ),

                            "image_link": (
                                image_link
                                if image_link
                                else "N/A"
                            ),

                            "product_link": product_url,

                            "organization_id": (
                                "Dealwallet"
                            ),

                            "store_id": (
                                "Flipkart"
                            ),

                            "categories_id": (
                                category
                            ),
                            "created_at": timestamp,
                        }



                        # =====================================
                        # ADD PRODUCT
                        # =====================================

                        products.append(
                            product_data
                        )

                        page_count += 1
                        category_product_count += 1

                        print(
                            f"[INFO] Category progress: "
                            f"{category_product_count}/"
                            f"{MAX_PRODUCTS_PER_CATEGORY}"
                        )


                    except Exception as e:

                        print(
                            "[ERROR] Product "
                            "processing failed: "
                            f"{e}"
                        )

                        continue


                # =============================================
                # PAGE SUMMARY
                # =============================================

                print(
                    f"[INFO] Valid products "
                    f"from page {page}: "
                    f"{page_count}"
                )

                print(
                    f"[INFO] Total products "
                    f"so far: {len(products)}"
                )

                print(
                    f"[INFO] Category total: "
                    f"{category_product_count}/"
                    f"{MAX_PRODUCTS_PER_CATEGORY}"
                )

                # Do not wait or request another page when the
                # category limit has already been reached.
                if category_product_count >= MAX_PRODUCTS_PER_CATEGORY:
                    print(
                        f"[INFO] Finished category '{category}' "
                        f"with {category_product_count} products."
                    )
                    break

                await asyncio.sleep(0.5)

            print(
                f"[INFO] Category '{category}' completed with "
                f"{category_product_count} valid products."
            )


    # ========================================================
    # FINAL SUMMARY
    # ========================================================

    print("\n" + "=" * 80)

    print(
        f"[SUCCESS] Total products scraped: "
        f"{len(products)}"
    )

    print("=" * 80)

    return products


# ============================================================
# WINDOWS / PLAYWRIGHT PROCESS
# ============================================================

def run_crawl4ai_process():

    """
    Run Crawl4AI inside a separate process.

    Required on Windows because Playwright
    needs the Proactor event loop for
    subprocess support.
    """

    import sys

    if sys.platform == "win32":

        asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )

    return asyncio.run(
        scrape_flipkart_async()
    )


# ============================================================
# SCRAPY SPIDER
# ============================================================

class FlipkartSpider(scrapy.Spider):

    name = "flipkart"

    allowed_domains = [
        "flipkart.com",
        "www.flipkart.com"
    ]

    custom_settings = {

        "CONCURRENT_REQUESTS": 1,

        "DOWNLOAD_DELAY": 1,

    }


    async def start(self):

        """
        Scrapy 2.18 compatible async start.

        Crawl4AI runs in a separate process so
        Playwright works correctly on Windows.
        """

        print(
            "\n" + "=" * 80
        )

        print(
            "Starting Flipkart Spider"
        )

        print(
            "=" * 80
        )


        loop = asyncio.get_running_loop()


        # ====================================================
        # RUN CRAWL4AI IN SEPARATE PROCESS
        # ====================================================

        with ProcessPoolExecutor(
            max_workers=1
        ) as executor:

            products = await loop.run_in_executor(
                executor,
                run_crawl4ai_process
            )


        # ====================================================
        # YIELD PRODUCTS TO DEALWALLET PIPELINE
        # ====================================================

        for product in products:

            # ================================================
            # FINAL DATA TYPE CHECK
            # ================================================

            print(
                "\n[DATA TYPE CHECK]"
            )

            print(
                "name           :",
                type(
                    product.get("name")
                ).__name__
            )

            print(
                "price          :",
                type(
                    product.get("price")
                ).__name__
            )

            print(
                "original_price :",
                type(
                    product.get("original_price")
                ).__name__
            )

            print(
                "discount       :",
                type(
                    product.get("discount")
                ).__name__
            )

            print(
                "ratings        :",
                type(
                    product.get("ratings")
                ).__name__
            )

            print(
                "currency       :",
                type(
                    product.get("currency")
                ).__name__
            )

            print(
                "description    :",
                type(
                    product.get("description")
                ).__name__
            )

            print(
                "image_link     :",
                type(
                    product.get("image_link")
                ).__name__
            )

            print(
                "product_link   :",
                type(
                    product.get("product_link")
                ).__name__
            )

            print(
                "organization_id:",
                type(
                    product.get("organization_id")
                ).__name__
            )

            print(
                "store_id       :",
                type(
                    product.get("store_id")
                ).__name__
            )

            print(
                "categories_id  :",
                type(
                    product.get("categories_id")
                ).__name__
            )


            # ================================================
            # YIELD NORMAL DICT
            # ================================================

            yield product