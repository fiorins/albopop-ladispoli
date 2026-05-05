from dotenv import load_dotenv
from lxml import etree
from feedgen.feed import FeedGenerator

load_dotenv()


# ── Configs ────────────────────────────────────────────────────────────────────

FEED_FILE = "../feed.xml"
FEED_URL = "https://fiorins.github.io/albopop-ladispoli/feed.xml"


# ── Helpers ───────────────────────────────────────────────────────────────────
def add_channel_extras(channel):
    categories = [
        ("http://albopop.it/specs#channel-category-type", "Comune"),
        ("http://albopop.it/specs#channel-category-municipality", "Ladispoli"),
        ("http://albopop.it/specs#channel-category-province", "Roma"),
        ("http://albopop.it/specs#channel-category-region", "Lazio"),
        ("http://albopop.it/specs#channel-category-latitude", "41.95326914"),
        ("http://albopop.it/specs#channel-category-longitude", "12.08091316"),
        ("http://albopop.it/specs#channel-category-country", "Italia"),
        ("http://albopop.it/specs#channel-category-name", "Comune di Ladispoli"),
        ("http://albopop.it/specs#channel-category-uid", "istat:058116"),
    ]

    webmaster = channel.find("webMaster")
    insert_index = (
        list(channel).index(webmaster) + 1 if webmaster is not None else len(channel)
    )

    # Insert categories
    for i, (domain, value) in enumerate(categories):
        cat = etree.Element("category")
        cat.set("domain", domain)
        cat.text = value
        channel.insert(insert_index + i, cat)

    # Insert xhtml meta right after categories
    XHTML_NS = "http://www.w3.org/1999/xhtml"
    meta = etree.Element(
        f"{{{XHTML_NS}}}meta", attrib={"name": "robots", "content": "noindex"}
    )

    channel.insert(insert_index + len(categories), meta)


def add_item_categories(item, entry):
    categories = [
        (
            "http://albopop.it/specs#item-category-pubStart",
            str(entry.get("pub_start_alt", "")),
        ),
        (
            "http://albopop.it/specs#item-category-pubEnd",
            str(entry.get("pub_end_alt", "")),
        ),
        ("http://albopop.it/specs#item-category-uid", str(entry.get("registry", ""))),
        ("http://albopop.it/specs#item-category-type", str(entry.get("type", ""))),
        ("item-category-subType", str(entry.get("sub_type", ""))),
        ("item-category-entry", str(entry.get("entry_id", ""))),
        (
            "item-category-attachments",
            str(entry.get("att_count", "")),
        ),
        ("item-category-attachBoxUrl", str(entry.get("box_file_link", ""))),
    ]

    guid = item.find("guid")
    insert_index = list(item).index(guid) + 1 if guid is not None else len(item)

    for i, (domain, value) in enumerate(categories):
        cat = etree.Element("category")
        cat.set("domain", domain)
        cat.text = value
        item.insert(insert_index + i, cat)


def fix_item(item, entry):
    desc = item.find("description")
    if desc is not None:
        # desc.text = etree.CDATA(f"📚 Allegati totali: {entry.get('att_count', '')}")
        desc.text = f"📚 Allegati totali: {entry['att_count']}"

    guid = item.find("guid")
    if guid is not None:
        guid.set("isPermaLink", "true")

    add_item_categories(item, entry)

    # Reorder pubDate before the guid
    pub_date = item.find("pubDate")
    guid = item.find("guid")

    if pub_date is not None and guid is not None:
        item.remove(pub_date)
        guid_index = list(item).index(guid)
        item.insert(guid_index, pub_date)


def generate_rss(entries):
    fg = FeedGenerator()
    fg.id(FEED_URL)
    fg.title("AlboPOP - Comune - Ladispoli")
    fg.link(href="https://fiorins.github.io/albopop-ladispoli/feed")
    fg.description("*non ufficiale* RSS feed dell'Albo Pretorio di Ladispoli")
    fg.language("it")

    fg.docs("http://albopop.it/comune/ladispoli/")
    fg.webMaster("davidefiorini@outlook.com (Davide Fiorini)")

    for entry in entries:
        fe = fg.add_entry()
        fe.id(entry["entry_id"])
        fe.title(entry["title"])
        fe.link(href=entry["entry_url"])
        fe.published(entry["pub_start"])
        fe.description(f"📚 Allegati totali: {entry['att_count']}")
        # if e.get("box_file_link") and entry["box_file_link"] != "non presente":
        if entry.get("box_file_link"):
            fe.enclosure(entry["box_file_link"], 0, "application/pdf")
        else:
            fe.enclosure("", 0, "application/pdf")

    # Create the xml before save
    rss_xml = fg.rss_str(pretty=True)

    root = etree.fromstring(rss_xml)
    channel = root.find("channel")

    # Add custom categories
    add_channel_extras(channel)
    items = channel.findall("item")

    for item, entry in zip(items, entries):
        fix_item(item, entry)

    etree.indent(root, space="  ")

    # Save the final file
    tree = etree.ElementTree(root)
    tree.write(FEED_FILE, pretty_print=True, xml_declaration=True, encoding="utf-8")
