"""Fingerprints for technology detection. Lowercase substrings or regexes matched
against raw HTML. Extend freely — this is the 'knowledge base' of the auditor."""
from __future__ import annotations

import re

CMS: dict[str, list[str]] = {
    "WordPress": ["wp-content/", "wp-includes/", 'name="generator" content="wordpress'],
    "Wix": ["static.wixstatic.com", "wix.com website builder", "_wixcssimports"],
    "Squarespace": ["static1.squarespace.com", "squarespace.com/universal"],
    "Shopify": ["cdn.shopify.com", "shopify.theme"],
    "Webflow": ["webflow.com/css", "data-wf-page", "assets.website-files.com"],
    "GoDaddy Builder": ["img1.wsimg.com", "godaddy website builder"],
    "Weebly": ["weebly.com", "editmysite.com"],
    "Duda": ["multiscreensite.com", "dudaone", "cdn-website.com"],
    "Joomla": ['content="joomla', "/media/jui/"],
    "Drupal": ["drupal.settings", "/sites/default/files/"],
    "Framer": ["framerusercontent.com", "framer.com/m/"],
    "HubSpot CMS": ["hs-sites.com", "hubspotusercontent"],
    "GoHighLevel": ["leadconnectorhq.com", "msgsndr.com"],
}

# Free-builder subdomains → strong "needs a real website" signal
BUILDER_SUBDOMAINS = [
    "wixsite.com", "squarespace.com", "weebly.com", "godaddysites.com", "business.site",
    "wordpress.com", "blogspot.com", "webflow.io", "carrd.co", "square.site", "site123.me",
    "jimdosite.com", "mystrikingly.com", "my.canva.site",
]

CHAT_WIDGETS: dict[str, list[str]] = {
    "Intercom": ["widget.intercom.io", "intercomsettings"],
    "Drift": ["js.driftt.com", "drift.com/include"],
    "Tawk.to": ["embed.tawk.to"],
    "Crisp": ["client.crisp.chat"],
    "Zendesk Chat": ["static.zdassets.com/ekr", "zopim"],
    "LiveChat": ["cdn.livechatinc.com"],
    "HubSpot Chat": ["js.usemessages.com"],
    "Tidio": ["code.tidio.co"],
    "Freshchat": ["wchat.freshchat.com", "freshworks.com/widget"],
    "Olark": ["static.olark.com"],
    "Podium": ["connect.podium.com"],
    "Birdeye": ["birdeye.com/embed"],
    "LeadConnector Chat": ["widgets.leadconnectorhq.com"],
    "ManyChat": ["widget.manychat.com"],
    "Chatbase": ["chatbase.co/embed"],
    "Botpress": ["cdn.botpress.cloud"],
    "Voiceflow": ["cdn.voiceflow.com"],
    "Smartsupp": ["smartsuppchat.com"],
    "Userlike": ["userlike-cdn-widgets"],
    "Gorgias": ["config.gorgias.chat"],
}

BOOKING: dict[str, list[str]] = {
    "Calendly": ["calendly.com"],
    "Acuity": ["acuityscheduling.com"],
    "Zocdoc": ["zocdoc.com"],
    "Setmore": ["setmore.com"],
    "Square Appointments": ["squareup.com/appointments", "square.site/book"],
    "Booksy": ["booksy.com"],
    "Fresha": ["fresha.com"],
    "OpenTable": ["opentable.com"],
    "SimplyBook": ["simplybook.me"],
    "NexHealth": ["nexhealth.com"],
    "LocalMed": ["localmed.com"],
    "Mindbody": ["mindbodyonline.com"],
    "Housecall Pro": ["housecallpro.com"],
    "Jobber": ["getjobber.com", "clienthub.getjobber.com"],
}

ANALYTICS = {
    "ga4": re.compile(r"googletagmanager\.com/gtag/js\?id=g-|gtag\(\s*['\"]config['\"]\s*,\s*['\"]g-", re.I),
    "gtm": re.compile(r"googletagmanager\.com/gtm\.js|gtm-[a-z0-9]{5,}", re.I),
    "google_ads": re.compile(r"googleadservices\.com|googleads\.g\.doubleclick\.net|['\"]aw-\d{6,}", re.I),
    "meta_pixel": re.compile(r"connect\.facebook\.net/[^\"']*/fbevents\.js|fbq\(\s*['\"]init", re.I),
    "tiktok_pixel": re.compile(r"analytics\.tiktok\.com", re.I),
    "linkedin_insight": re.compile(r"snap\.licdn\.com/li\.lms-analytics", re.I),
}

SOCIAL_PATTERNS = {
    "facebook": re.compile(r"https?://(?:www\.|m\.)?facebook\.com/(?!sharer|share|plugins|tr\b|dialog)[\w.\-/%]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/(?!p/|share)[\w.\-]+", re.I),
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[\w.\-%]+", re.I),
    "twitter": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/(?!share|intent|home)[\w]+", re.I),
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(?:channel/|c/|@|user/)[\w.\-]+", re.I),
    "tiktok": re.compile(r"https?://(?:www\.)?tiktok\.com/@[\w.\-]+", re.I),
    "whatsapp": re.compile(r"https?://(?:wa\.me|api\.whatsapp\.com/send)[^\s\"'<>]*", re.I),
}

LOCAL_SCHEMA_TYPES = {
    "localbusiness", "dentist", "attorney", "legalservice", "medicalbusiness", "physician",
    "plumber", "electrician", "hvacbusiness", "roofingcontractor", "homeandconstructionbusiness",
    "restaurant", "foodestablishment", "realestateagent", "autorepair", "beautysalon",
    "hairsalon", "dayspa", "healthclub", "lodgingbusiness", "hotel", "store", "professionalservice",
    "medicalclinic", "accountingservice", "insuranceagency", "movingcompany", "locksmith",
}
