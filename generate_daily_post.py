#!/usr/bin/env python3
"""
Daily WordPress content bot.

What this script does, in order:
  1. Asks WordPress for your most recent post titles (so Claude doesn't repeat topics)
  2. Asks Claude to write a blog post: title, HTML body with image placeholders,
     and a list of 1-5 image prompts (the first is the main/featured image)
  3. Asks an image API to generate each image
  4. Resizes the main/featured image to a fixed 400x300 and uploads all images to WordPress
  5. Drops each image into the post content at the spot Claude chose for it
     (the main image floats right; extra images sit inline, full width)
  6. Creates a new WordPress post (draft by default) with the first image as the featured image

Configuration is via environment variables (see README.md for setup):
  ANTHROPIC_API_KEY   - your Claude API key
  OPENAI_API_KEY      - your OpenAI API key (used only for image generation)
  WP_BASE_URL         - e.g. https://yourblog.com  (no trailing slash)
  WP_USERNAME         - your WordPress username
  WP_APP_PASSWORD     - a WordPress "Application Password" (NOT your login password)

Optional environment variables:
  POST_STATUS         - "draft" (default, recommended) or "publish"
  SITE_TOPIC          - a short description of what your site is about
  SITE_VOICE          - a short description of your desired tone/voice
  SITE_INSTRUCTIONS   - freeform rules/requirements, edit any time, no code changes needed
  IMAGE_QUALITY       - "low", "medium" (default), or "high"
"""

import base64
import io
import os
import sys
import json
import re
import datetime
import urllib.request
import urllib.error

from PIL import Image


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
WP_BASE_URL = os.environ.get("WP_BASE_URL", "").rstrip("/")
WP_USERNAME = os.environ.get("WP_USERNAME")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD")

POST_STATUS = os.environ.get("POST_STATUS", "draft")  # "draft" or "publish"
SITE_TOPIC = os.environ.get("SITE_TOPIC", "a general interest blog")
SITE_VOICE = os.environ.get("SITE_VOICE", "friendly, clear, and informative")
SITE_INSTRUCTIONS = os.environ.get("SITE_INSTRUCTIONS", "")
IMAGE_QUALITY = os.environ.get("IMAGE_QUALITY", "medium")

CLAUDE_MODEL = "claude-sonnet-5"
IMAGE_MODEL = "gpt-image-1-mini"  # cheap + good enough for blog images
MAIN_IMAGE_SIZE = (400, 300)      # fixed featured/main image dimensions, per site rules
MAX_IMAGES = 1

# Some hosts/security plugins block the default Python User-Agent as bot-like.
# Sending a normal browser-style UA avoids tripping those filters.
DEFAULT_UA = "Mozilla/5.0 (compatible; DailyPostBot/1.0; +https://github.com/)"

REQUIRED_VARS = {
    "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "WP_BASE_URL": WP_BASE_URL,
    "WP_USERNAME": WP_USERNAME,
    "WP_APP_PASSWORD": WP_APP_PASSWORD,
}


def check_config():
    missing = [name for name, val in REQUIRED_VARS.items() if not val]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Small HTTP helpers (stdlib only, no extra dependencies beyond Pillow)
# ---------------------------------------------------------------------------

def http_post_json(url, payload, headers):
    data = json.dumps(payload).encode("utf-8")
    headers = {**headers, "User-Agent": DEFAULT_UA}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} error calling {url}:\n{body}", file=sys.stderr)
        raise


def http_get_json(url, headers):
    headers = {**headers, "User-Agent": DEFAULT_UA}
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wp_auth_header():
    token = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode("utf-8")).decode("utf-8")
    return {"Authorization": f"Basic {token}"}


def slugify(text, max_len=60):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "post-image"


# ---------------------------------------------------------------------------
# Step 1: recent post titles, so we don't repeat ourselves
# ---------------------------------------------------------------------------

def get_recent_titles(limit=10):
    url = f"{WP_BASE_URL}/wp-json/wp/v2/posts?per_page={limit}&_fields=title"
    try:
        posts = http_get_json(url, headers=wp_auth_header())
        return [re.sub("<.*?>", "", p["title"]["rendered"]).strip() for p in posts]
    except Exception as e:
        print(f"Warning: could not fetch recent titles ({e}). Continuing without them.", file=sys.stderr)
        return []


def get_categories():
    """Returns a list of {"id": int, "name": str} for all existing categories."""
    url = f"{WP_BASE_URL}/wp-json/wp/v2/categories?per_page=100&_fields=id,name"
    try:
        return http_get_json(url, headers=wp_auth_header())
    except Exception as e:
        print(f"Warning: could not fetch categories ({e}). Continuing without them.", file=sys.stderr)
        return []


# ---------------------------------------------------------------------------
# Step 2: ask Claude to write the post + decide on images
# ---------------------------------------------------------------------------

def generate_post(recent_titles, categories):
    avoid_list = "\n".join(f"- {t}" for t in recent_titles) if recent_titles else "(none yet)"
    today_str = datetime.date.today().strftime("%A, %B %d, %Y")
    category_list = ", ".join(c["name"] for c in categories) if categories else "(none exist yet)"

    system_prompt = f"""You are a content writer for a website about: {SITE_TOPIC}.
Your writing voice is: {SITE_VOICE}.
Today's date is: {today_str}.

Additional rules and instructions you must follow:
{SITE_INSTRUCTIONS if SITE_INSTRUCTIONS.strip() else "(none)"}

The site's existing categories are: {category_list}

You must respond with ONLY a JSON object (no markdown fences, no commentary) with exactly these keys:
  "title": a compelling, specific post title (not generic)
  "category": the single best-fitting category name for this post. Prefer reusing one of the
              existing categories listed above if one genuinely fits. Only propose a brand new
              category name if none of the existing ones make sense for this post's topic.
  "html_body": the full post body as simple HTML (use <p>, <h2>, <ul> as needed, no <html>/<body> tags).
               Insert the exact placeholder token [[IMAGE_1]] at the spot where the header image
               should appear (usually near the top).
  "images": a list containing exactly 1 object with:
               "prompt": a description for an AI image generator to create a REALISTIC,
                         PHOTOGRAPHIC image (like an actual photograph, not a painting,
                         illustration, cartoon, or digital art) that would make sense as the
                         header image for an article with this specific title and topic.
                         Be concrete about the real-world scene, subject, and setting implied
                         by the title -- avoid generic or abstract imagery. Explicitly include
                         words like "photograph" or "photorealistic" in the prompt itself.
                         No text or words should appear in the image.
               "alt": short accessibility alt text describing the image

The post should be genuinely useful or interesting, around 500-800 words.
Do not write about any of these recent topics (already covered):
{avoid_list}
"""

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 3000,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": "Write today's post."}
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
    }

    result = http_post_json("https://api.anthropic.com/v1/messages", payload, headers)
    text = "".join(block["text"] for block in result["content"] if block["type"] == "text")

    # Claude is asked to return pure JSON; strip accidental code fences just in case.
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    post = json.loads(cleaned)

    images = post["images"][:MAX_IMAGES]
    if not images:
        raise ValueError("Claude returned zero images; expected at least 1.")

    category_name = post.get("category", "").strip()

    return post["title"], post["html_body"], images, category_name


# ---------------------------------------------------------------------------
# Step 3: generate each image
# ---------------------------------------------------------------------------

def generate_image_bytes(image_prompt):
    # Belt-and-suspenders: reinforce photorealism in code, not just via Claude's prompt wording.
    styled_prompt = (
        f"{image_prompt} "
        "Photorealistic photograph, natural lighting, realistic textures and detail, "
        "shot on a real camera -- not a painting, illustration, cartoon, or digital art."
    )
    payload = {
        "model": IMAGE_MODEL,
        "prompt": styled_prompt,
        "size": "1024x1024",
        "quality": IMAGE_QUALITY,
        "n": 1,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENAI_API_KEY}",
    }
    result = http_post_json("https://api.openai.com/v1/images/generations", payload, headers)
    b64_data = result["data"][0]["b64_json"]
    return base64.b64decode(b64_data)


def resize_and_crop(image_bytes, target_size):
    """Resize to cover target_size, then center-crop to it exactly."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    target_w, target_h = target_size
    src_w, src_h = img.size

    scale = max(target_w / src_w, target_h / src_h)
    new_w, new_h = round(src_w * scale), round(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    img = img.crop((left, top, left + target_w, top + target_h))

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ---------------------------------------------------------------------------
# Step 4: upload an image to WordPress
# ---------------------------------------------------------------------------

def upload_image_to_wp(image_bytes, filename, alt_text=""):
    url = f"{WP_BASE_URL}/wp-json/wp/v2/media"
    headers = {
        **wp_auth_header(),
        "Content-Type": "image/png",
        "Content-Disposition": f'attachment; filename="{filename}"',
        "User-Agent": DEFAULT_UA,
    }
    req = urllib.request.Request(url, data=image_bytes, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        media = json.loads(resp.read().decode("utf-8"))

    # Best-effort: set alt text on the uploaded media item (ignore failures).
    if alt_text:
        try:
            patch_url = f"{WP_BASE_URL}/wp-json/wp/v2/media/{media['id']}"
            patch_headers = {**wp_auth_header(), "Content-Type": "application/json", "User-Agent": DEFAULT_UA}
            req2 = urllib.request.Request(
                patch_url,
                data=json.dumps({"alt_text": alt_text}).encode("utf-8"),
                headers=patch_headers,
                method="POST",  # WP REST API accepts POST for partial updates
            )
            urllib.request.urlopen(req2)
        except Exception as e:
            print(f"Warning: could not set alt text ({e})", file=sys.stderr)

    return media["id"], media["source_url"]


# ---------------------------------------------------------------------------
# Step 5: build the images, insert them into the content
# ---------------------------------------------------------------------------

def build_images_and_insert(title, html_body, image_specs):
    featured_media_id = None
    slug_base = slugify(title)

    for i, spec in enumerate(image_specs, start=1):
        print(f"  Generating image {i}/{len(image_specs)}...")
        raw_bytes = generate_image_bytes(spec["prompt"])

        if i == 1:
            # Main/featured image: fixed 400x300, floated right in the content.
            final_bytes = resize_and_crop(raw_bytes, MAIN_IMAGE_SIZE)
            filename = f"{slug_base}.png"
            media_id, source_url = upload_image_to_wp(final_bytes, filename, spec.get("alt", ""))
            featured_media_id = media_id
            img_tag = (
                f'<img src="{source_url}" alt="{spec.get("alt", "")}" '
                f'width="{MAIN_IMAGE_SIZE[0]}" height="{MAIN_IMAGE_SIZE[1]}" '
                f'style="float:right; margin:0 0 1em 1.5em; max-width:100%;" />'
            )
        else:
            filename = f"{slug_base}-{i}.png"
            media_id, source_url = upload_image_to_wp(raw_bytes, filename, spec.get("alt", ""))
            img_tag = (
                f'<img src="{source_url}" alt="{spec.get("alt", "")}" '
                f'style="max-width:100%; height:auto; display:block; margin:1.5em auto;" />'
            )

        placeholder = f"[[IMAGE_{i}]]"
        if placeholder in html_body:
            html_body = html_body.replace(placeholder, img_tag)
        elif i == 1:
            # Make sure the main image always appears even if Claude forgot the placeholder.
            html_body = img_tag + html_body
        else:
            html_body += img_tag

    # Clean up any unused placeholders (e.g. Claude listed fewer images than placeholders).
    html_body = re.sub(r"\[\[IMAGE_\d+\]\]", "", html_body)

    return featured_media_id, html_body


def get_or_create_category_id(category_name, known_categories):
    """Matches category_name against known_categories (case-insensitive); creates it on
    WordPress if no match exists. Returns a category ID, or None if it can't be resolved."""
    if not category_name:
        return None

    for cat in known_categories:
        if cat["name"].strip().lower() == category_name.strip().lower():
            return cat["id"]

    # No existing match -- create the new category.
    print(f"  Category '{category_name}' doesn't exist yet, creating it...")
    url = f"{WP_BASE_URL}/wp-json/wp/v2/categories"
    headers = {**wp_auth_header(), "Content-Type": "application/json"}
    try:
        result = http_post_json(url, {"name": category_name}, headers)
        return result["id"]
    except Exception as e:
        print(f"Warning: could not create category '{category_name}' ({e}). Post will be uncategorized.", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Step 6: create the WordPress post
# ---------------------------------------------------------------------------

def create_wp_post(title, html_body, featured_media_id, category_id):
    url = f"{WP_BASE_URL}/wp-json/wp/v2/posts"
    payload = {
        "title": title,
        "content": html_body,
        "status": POST_STATUS,
        "featured_media": featured_media_id,
    }
    if category_id:
        payload["categories"] = [category_id]
    headers = {**wp_auth_header(), "Content-Type": "application/json"}
    return http_post_json(url, payload, headers)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    check_config()

    print("Fetching recent post titles...")
    recent_titles = get_recent_titles()

    print("Fetching existing categories...")
    categories = get_categories()

    print("Generating post text with Claude...")
    title, html_body, image_specs, category_name = generate_post(recent_titles, categories)
    print(f"  Title: {title}")
    print(f"  Category: {category_name or '(none chosen)'}")
    print(f"  Planned images: {len(image_specs)}")

    print("Generating and uploading images...")
    featured_media_id, html_body = build_images_and_insert(title, html_body, image_specs)

    category_id = get_or_create_category_id(category_name, categories)

    print(f"Creating WordPress post (status={POST_STATUS})...")
    post = create_wp_post(title, html_body, featured_media_id, category_id)

    print(f"Done! Post ID {post['id']}: {post['link']}")


if __name__ == "__main__":
    main()
