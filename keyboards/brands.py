"""Brand catalog for product shop buttons (real logos via custom emoji pack)."""
from __future__ import annotations

# 42 most common digital-product brands (slug -> display name)
BRAND_LABELS: dict[str, str] = {
    "adobe": "Adobe Creative Cloud",
    "amazon": "Amazon Prime",
    "antigravity": "Antigravity",
    "apple": "Apple / iCloud+",
    "canva": "Canva",
    "capcut": "CapCut",
    "chatgpt": "ChatGPT / OpenAI",
    "claude": "Claude",
    "coursera": "Coursera",
    "cursor": "Cursor",
    "discord": "Discord Nitro",
    "disney": "Disney+",
    "dropbox": "Dropbox",
    "duolingo": "Duolingo",
    "elevenlabs": "ElevenLabs",
    "expressvpn": "ExpressVPN",
    "figma": "Figma",
    "framer": "Framer",
    "gemini": "Google Gemini",
    "github": "GitHub / Copilot",
    "grammarly": "Grammarly",
    "grok": "Grok / xAI",
    "jetbrains": "JetBrains",
    "kiro": "Kiro",
    "linkedin": "LinkedIn Premium",
    "midjourney": "Midjourney",
    "microsoft": "Microsoft 365",
    "mobbin": "Mobbin",
    "manus": "Manus",
    "meshy": "Meshy AI",
    "netflix": "Netflix",
    "nordvpn": "NordVPN",
    "notion": "Notion",
    "perplexity": "Perplexity",
    "picsart": "Picsart",
    "playstation": "PlayStation Plus",
    "quillbot": "Quillbot",
    "replit": "Replit",
    "slack": "Slack",
    "spotify": "Spotify",
    "steam": "Steam",
    "telegram": "Telegram Premium",
    "twitch": "Twitch",
    "wisprflow": "Wispr Flow",
    "xbox": "Xbox Game Pass",
    "youtube": "YouTube Premium",
    "zoom": "Zoom",
    # Dev / AI-SaaS brands (added from product catalog)
    "intercom": "Intercom",
    "supabase": "Supabase",
    "posthog": "PostHog",
    "linear": "Linear",
    "descript": "Descript",
    "n8n": "n8n",
    "railway": "Railway",
    "warp": "Warp",
    "granola": "Granola",
    "gamma": "Gamma",
    "chatprd": "ChatPRD",
    "bolt": "Bolt",
    "gumloop": "Gumloop",
}

# Iconify "logos" collection — full-color SVGs (https://iconify.design/logos/)
ICONIFY_LOGO: dict[str, str] = {
    "adobe": "adobe",
    "apple": "apple",
    "chatgpt": "openai",
    "claude": "claude",
    "discord": "discord",
    "dropbox": "dropbox",
    "figma": "figma",
    "framer": "framer",
    "gemini": "google-gemini",
    "github": "github",
    "grammarly": "grammarly",
    "jetbrains": "jetbrains",
    "linkedin": "linkedin",
    "midjourney": "midjourney",
    "microsoft": "microsoft",
    "netflix": "netflix",
    "notion": "notion",
    "perplexity": "perplexity",
    "replit": "replit",
    "slack": "slack",
    "spotify": "spotify",
    "steam": "steam",
    "telegram": "telegram",
    "twitch": "twitch",
    "youtube": "youtube",
    "zoom": "zoom",
}

# simple-icons slug (MIT SVG). Omit entry → favicon fallback in setup script.
SIMPLE_ICON_SLUG: dict[str, str] = {
    "adobe": "adobe",
    "amazon": "amazon",
    "apple": "apple",
    "canva": "canva",
    "chatgpt": "openai",
    "claude": "claude",
    "discord": "discord",
    "disney": "disneyplus",
    "dropbox": "dropbox",
    "duolingo": "duolingo",
    "expressvpn": "expressvpn",
    "figma": "figma",
    "framer": "framer",
    "gemini": "googlegemini",
    "github": "github",
    "grammarly": "grammarly",
    "jetbrains": "jetbrains",
    "linkedin": "linkedin",
    "microsoft": "microsoft",
    "netflix": "netflix",
    "nordvpn": "nordvpn",
    "notion": "notion",
    "perplexity": "perplexity",
    "picsart": "picsart",
    "playstation": "playstation",
    "replit": "replit",
    "slack": "slack",
    "spotify": "spotify",
    "steam": "steam",
    "telegram": "telegram",
    "twitch": "twitch",
    "xbox": "xbox",
    "youtube": "youtube",
    "zoom": "zoom",
}

# Direct PNG URLs for brands where iconify/simple-icons are unclear at emoji size.
BRAND_ICON_URL: dict[str, str] = {
    "grok": "https://unpkg.com/@lobehub/icons-static-png@latest/dark/grok.png",
    # The iconify/simple-icons "coursera" entries are the wordmark / bare
    # letterform. The recognisable brand mark is the solid blue circle with a
    # white "C" — that's the site favicon, served crisply at 128px here.
    "coursera": "https://www.google.com/s2/favicons?domain=coursera.org&sz=128",
    # Iconify `logos:google-gemini` is the wordmark and `-icon` variant doesn't
    # exist; lobehub's color PNG is the recognisable 4-point gradient star.
    "gemini": "https://unpkg.com/@lobehub/icons-static-png@latest/light/gemini-color.png",
    # Gradient arch from Google favicon service (not the generic blue dot fallback).
    "antigravity": "https://www.google.com/s2/favicons?domain=antigravity.google&sz=128",
}

# Favicon domain when Simple Icons has no slug or SVG 404
FAVICON_DOMAIN: dict[str, str] = {
    "antigravity": "antigravity.google",
    "capcut": "capcut.com",
    "coursera": "coursera.org",
    "cursor": "cursor.com",
    "elevenlabs": "elevenlabs.io",
    "midjourney": "midjourney.com",
    "mobbin": "mobbin.com",
    "manus": "manus.im",
    "meshy": "meshy.ai",
    "picsart": "picsart.com",
    "quillbot": "quillbot.com",
    "claude": "claude.ai",
    "chatgpt": "openai.com",
    "gemini": "gemini.google.com",
    "grok": "x.ai",
    "kiro": "kiro.dev",
    "wisprflow": "wisprflow.ai",
    # Dev / AI-SaaS brands — favicon/apple-touch-icon is the clean square logo.
    "intercom": "intercom.com",
    "supabase": "supabase.com",
    "posthog": "posthog.com",
    "linear": "linear.app",
    "descript": "descript.com",
    "n8n": "n8n.io",
    "railway": "railway.app",
    "warp": "warp.dev",
    "granola": "granola.ai",
    "gamma": "gamma.app",
    "chatprd": "chatprd.ai",
    "bolt": "bolt.new",
    "gumloop": "gumloop.com",
}

NAME_KEYWORDS: dict[str, tuple[str, ...]] = {
    "adobe": ("adobe", "creative cloud", "photoshop", "premiere", "lightroom"),
    "amazon": ("amazon prime", "amazon", "prime video"),
    "antigravity": ("antigravity", "anti gravity", "google antigravity"),
    "apple": ("apple", "icloud", "apple music", "apple tv"),
    "canva": ("canva",),
    "capcut": ("capcut", "cap cut"),
    "chatgpt": ("chatgpt", "chat gpt", "gpt plus", "gpt-4", "openai"),
    "claude": ("claude", "anthropic"),
    "coursera": ("coursera", "coursera plus"),
    "cursor": ("cursor",),
    "discord": ("discord", "nitro"),
    "disney": ("disney", "disney+"),
    "dropbox": ("dropbox",),
    "duolingo": ("duolingo",),
    "elevenlabs": ("elevenlab", "eleven labs", "11labs"),
    "expressvpn": ("expressvpn", "express vpn"),
    "figma": ("figma",),
    "framer": ("framer",),
    "gemini": ("gemini", "google ai", "bard"),
    "github": ("github", "copilot"),
    "grammarly": ("grammarly",),
    "grok": ("grok", "xai"),
    "jetbrains": ("jetbrains", "pycharm", "webstorm"),
    "kiro": ("kiro",),
    "linkedin": ("linkedin",),
    "midjourney": ("midjourney", "mid journey"),
    "microsoft": ("microsoft", "office 365", "m365"),
    "mobbin": ("mobbin",),
    "manus": ("manus",),
    "meshy": ("meshy", "meshy ai", "meshyai"),
    "netflix": ("netflix",),
    "nordvpn": ("nordvpn", "nord vpn"),
    "notion": ("notion",),
    "perplexity": ("perplexity",),
    "picsart": ("picsart", "pics art"),
    "playstation": ("playstation", "ps plus", "psn"),
    "quillbot": ("quillbot", "quill bot"),
    "replit": ("replit",),
    "slack": ("slack",),
    "spotify": ("spotify",),
    "steam": ("steam",),
    "telegram": ("telegram premium", "telegram"),
    "twitch": ("twitch",),
    "wisprflow": ("wispr flow", "wisprflow", "wispr"),
    "xbox": ("xbox", "game pass"),
    "youtube": ("youtube", "yt premium"),
    "zoom": ("zoom",),
    # Dev / AI-SaaS brands (added from product catalog)
    "intercom": ("intercom",),
    "supabase": ("supabase", "supa base"),
    "posthog": ("posthog", "post hog"),
    "linear": ("linear",),
    "descript": ("descript",),
    "n8n": ("n8n",),
    "railway": ("railway", "rail way"),
    "warp": ("warp",),
    "granola": ("granola",),
    "gamma": ("gamma",),
    "chatprd": ("chatprd", "chat prd"),
    "bolt": ("bolt",),
    "gumloop": ("gumloop", "gum loop"),
}


def guess_brand_from_name(name: str) -> str:
    n = (name or "").lower()
    for slug, keywords in NAME_KEYWORDS.items():
        if any(kw in n for kw in keywords):
            return slug
    return ""


def product_brand(product) -> str:
    if "brand" in product.keys():
        explicit = str(product["brand"] or "").strip().lower()
        if explicit and explicit in BRAND_LABELS:
            return explicit
    return guess_brand_from_name(str(product["name"] or ""))


def brand_choices_sorted() -> list[tuple[str, str]]:
    return sorted(BRAND_LABELS.items(), key=lambda x: x[1].lower())


def admin_preview_url(slug: str) -> str:
    """Preview image for admin logo picker (served from /brand-logos/ on VPS)."""
    return f"/brand-logos/{slug}.png"
