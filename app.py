"""
AI Mood Board Generator for Books
=================================

Type in any book title and get back:
  - the overall "vibe" of the story
  - a color palette (with the reasoning behind each color)
  - aesthetic keywords
  - a grid of real Pinterest-style photos that capture the mood

How it works (two steps):
  1. Claude "reads" the book and returns a structured mood-board concept.
  2. Those concepts become search queries against Unsplash, which returns real photos.

Run it with:  streamlit run app.py
"""

import os
from io import BytesIO
from typing import List

import requests
import streamlit as st
from anthropic import Anthropic
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Data shapes
#
# These Pydantic models describe EXACTLY what we want Claude to return. We hand
# them to Claude as a JSON schema, so the response always comes back in this
# shape instead of as free-form prose we'd have to parse ourselves.
# ---------------------------------------------------------------------------


class PaletteColor(BaseModel):
    hex: str = Field(description="A hex color code like #1A2B3C")
    name: str = Field(description="A short evocative name for the color, e.g. 'Neon Dusk'")
    reason: str = Field(description="One sentence on why this color fits the book")


class Aesthetic(BaseModel):
    keyword: str = Field(description="A short aesthetic tag, e.g. 'dark academia' or 'solarpunk'")
    reason: str = Field(description="One sentence on why this aesthetic fits the book")


class Recommendation(BaseModel):
    title: str = Field(description="Title of a book with a similar mood or vibe")
    author: str = Field(description="Author of the recommended book")
    description: str = Field(description="A one-sentence description of the recommended book")
    why_similar: List[str] = Field(
        description=(
            "Exactly 3 short bullet points on how this book is similar to the input book, "
            "spanning aspects like characters, vibe, tone, and conflict"
        )
    )


class MoodBoard(BaseModel):
    book_title: str = Field(description="The book's title as you understand it")
    author: str = Field(description="The author, if you know it; otherwise 'Unknown'")
    overall_vibe: str = Field(description="A short, evocative paragraph (2-4 sentences) on the mood of the story")
    essence_quote: str = Field(
        description="One short, memorable quote from the book that captures the essence of the story"
    )
    palette: List[PaletteColor] = Field(
        description="The 4 colors most relevant to the book's themes, ordered most relevant first"
    )
    aesthetics: List[Aesthetic] = Field(description="3 to 5 aesthetic keywords")
    image_queries: List[str] = Field(
        description=(
            "5 to 8 short visual search phrases (2-4 words each) describing concrete "
            "scenes, objects, or moods to find photos for, e.g. 'neon futuristic city' "
            "or 'figures in black robes'. Avoid the book title or character names."
        )
    )
    recommendations: List[Recommendation] = Field(
        description="3 books with a similar mood or vibe, for readers who liked this one"
    )


class RecDetail(BaseModel):
    """Richer detail for a recommended book, generated on demand for the pop-up."""

    description: str = Field(
        description="A rich 2-3 sentence description of the book's premise and mood"
    )
    palette: List[PaletteColor] = Field(
        description="The 3 colors most relevant to the book's themes, ordered most relevant first"
    )


SYSTEM_PROMPT = (
    "You are an expert in literary analysis and visual design, who specializes in translating the feeling "
    "of a novel into colors, aesthetics, and imagery. You think about tone, setting, "
    "color symbolism, and atmosphere -- not just plot. Your image search phrases must "
    "be concrete and photographable (places, objects, lighting, textures, silhouettes), "
    "because they will be fed to a stock-photo search engine. Never include the book "
    "title, author name, or character names in the search phrases. "
    "When recommending similar books, pick ones that share the mood, tone, or atmosphere "
    "-- not merely the same genre -- and never recommend the input book itself."
)


# ---------------------------------------------------------------------------
# Step 1: ask Claude to design the mood board
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False, persist="disk")
def generate_mood_board(book_title: str, anthropic_api_key: str) -> MoodBoard:
    """Call Claude and return a validated MoodBoard.

    Cached to disk by (book_title, key), so a given book is generated once ever —
    even across app restarts — rather than re-spending on every run.
    """
    client = Anthropic(api_key=anthropic_api_key)

    response = client.messages.parse(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        # Adaptive thinking at low effort: enough reasoning about tone/symbolism,
        # but trimmed to keep token spend (and cost) down.
        thinking={"type": "adaptive"},
        output_config={"effort": "low"},
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f'Design a mood board for the book "{book_title}". '
                    "Capture its atmosphere, not a literal scene summary."
                ),
            }
        ],
        # This is what guarantees clean, structured output matching MoodBoard.
        output_format=MoodBoard,
    )

    return response.parsed_output


@st.cache_data(show_spinner=False, persist="disk")
def generate_rec_detail(title: str, author: str, anthropic_api_key: str) -> RecDetail:
    """A lightweight Claude call for a recommended book's richer description + palette.

    Used by the recommendation detail panel. Cached, so re-opening a book is free.
    Thinking is off here to keep the pop-up snappy.
    """
    client = Anthropic(api_key=anthropic_api_key)
    response = client.messages.parse(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        thinking={"type": "disabled"},
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f'For the book "{title}" by {author}, write a rich 2-3 sentence description '
                    "of its premise and mood, and give the 3 colors most relevant to its themes "
                    "(each with a name and a one-sentence reason it fits)."
                ),
            }
        ],
        output_format=RecDetail,
    )
    return response.parsed_output


# ---------------------------------------------------------------------------
# Step 2: turn the image queries into real photos via Unsplash
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False, persist="disk")
def search_unsplash(query: str, unsplash_access_key: str, per_query: int = 3) -> List[dict]:
    """Return a list of photo dicts for one search query.

    Each dict has: image_url, link, photographer, photographer_url.
    Unsplash's API guidelines require crediting the photographer, so we keep
    that info and display it under each image.
    """
    resp = requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "per_page": per_query, "orientation": "portrait"},
        headers={"Authorization": f"Client-ID {unsplash_access_key}"},
        timeout=15,
    )
    resp.raise_for_status()

    photos = []
    for result in resp.json().get("results", []):
        photos.append(
            {
                "image_url": result["urls"]["small"],       # for the on-screen grid
                "download_url": result["urls"]["regular"],  # higher-res, for the export
                "link": result["links"]["html"],
                "photographer": result["user"]["name"],
                "photographer_url": result["user"]["links"]["html"],
            }
        )
    return photos


# ---------------------------------------------------------------------------
# Look up the real book cover + publishing year (Open Library — no API key)
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False, persist="disk")
def fetch_book_info(title: str, author: str = "") -> dict:
    """Look up a book on Open Library for its cover image and first publish year.

    Returns {"cover_url": str | None, "year": int | None}. Never raises — on any
    problem it returns empties so the rest of the board still renders.
    """
    params = {
        "title": title,
        "limit": 1,
        "fields": "title,author_name,first_publish_year,cover_i",
    }
    if author and author.lower() != "unknown":
        params["author"] = author
    # Try twice — a single transient blip shouldn't make the cover silently vanish.
    for _attempt in range(2):
        try:
            resp = requests.get("https://openlibrary.org/search.json", params=params, timeout=15)
            resp.raise_for_status()
            docs = resp.json().get("docs", [])
            if not docs:
                return {"cover_url": None, "year": None}
            doc = docs[0]
            cover_id = doc.get("cover_i")
            # Open Library serves covers at .../b/id/<id>-{S,M,L}.jpg
            cover_url = f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg" if cover_id else None
            return {"cover_url": cover_url, "year": doc.get("first_publish_year")}
        except Exception:  # noqa: BLE001 - best-effort; retry once, then give up
            continue
    return {"cover_url": None, "year": None}


# ---------------------------------------------------------------------------
# Step 3: compose the title + palette + photos into one shareable PNG
# ---------------------------------------------------------------------------


def _hex_to_rgb(hex_str: str) -> tuple:
    """Turn '#1A2B3C' (or shorthand '#abc') into an (r, g, b) tuple."""
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _font(size: int, bold: bool = False):
    """Load a TrueType font, falling back gracefully if it isn't installed."""
    names = ["arialbd.ttf", "arial.ttf"] if bold else ["arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except Exception:  # noqa: BLE001 - font not found, try the next one
            continue
    try:
        return ImageFont.load_default(size)
    except Exception:  # noqa: BLE001 - very old Pillow without sized default
        return ImageFont.load_default()


@st.cache_data(show_spinner=False)
def build_board_image(title: str, author: str, palette: tuple, image_urls: tuple) -> bytes:
    """Draw the title, palette, and photos onto one canvas and return PNG bytes.

    Cached by its arguments, so it only rebuilds when the board actually changes.
    `palette` is a tuple of (hex, name); `image_urls` is a tuple of photo URLs.
    Note: these image fetches hit Unsplash's image CDN, not its API, so they do
    NOT count against the 50-requests/hour API limit.
    """
    W, margin, gap = 1200, 48, 16
    content_w = W - 2 * margin

    # White background with black text, so the image never blends into a palette color.
    bg, fg, muted = (255, 255, 255), (0, 0, 0), (110, 110, 110)

    title_font = _font(58, bold=True)
    author_font = _font(28)
    label_font = _font(19)
    footer_font = _font(18)

    # Measure text heights against a scratch canvas before sizing the real one.
    scratch = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def text_h(font) -> int:
        b = scratch.textbbox((0, 0), "Ag", font=font)
        return b[3] - b[1]

    # Work out the photo grid dimensions and the total canvas height.
    n = len(image_urls)
    cols = 3
    rows = max(1, (n + cols - 1) // cols)
    col_w = (content_w - (cols - 1) * gap) // cols
    cell_h = int(col_w * 1.25)  # portrait-ish cells
    swatch_h = 84

    y = margin
    title_top = y
    y += text_h(title_font) + 14 + text_h(author_font) + 28
    swatch_top = y
    y += swatch_h + 8 + text_h(label_font) + 28
    grid_top = y
    y += rows * cell_h + (rows - 1) * gap + 24
    footer_top = y
    H = y + text_h(footer_font) + margin

    canvas = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(canvas)

    # Title + author
    draw.text((margin, title_top), title, font=title_font, fill=fg)
    draw.text((margin, title_top + text_h(title_font) + 14), author, font=author_font, fill=muted)

    # Palette swatches with hex labels
    pn = len(palette)
    if pn:
        sw = (content_w - (pn - 1) * gap) / pn
        for i, (hex_code, _name) in enumerate(palette):
            x0 = margin + i * (sw + gap)
            try:
                color = _hex_to_rgb(hex_code)
            except Exception:  # noqa: BLE001
                color = (80, 80, 80)
            draw.rounded_rectangle(
                [int(x0), swatch_top, int(x0 + sw), swatch_top + swatch_h],
                radius=12, fill=color, outline=(210, 210, 210), width=1,
            )
            label = hex_code.upper()
            tw = draw.textlength(label, font=label_font)
            draw.text(
                (x0 + (sw - tw) / 2, swatch_top + swatch_h + 8), label, font=label_font, fill=muted
            )

    # Photo grid (downloaded and cropped to fill each cell)
    for idx, url in enumerate(image_urls):
        r, c = divmod(idx, cols)
        x = margin + c * (col_w + gap)
        ytop = grid_top + r * (cell_h + gap)
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            photo = Image.open(BytesIO(resp.content)).convert("RGB")
            photo = ImageOps.fit(photo, (col_w, cell_h))  # crop-to-fill, keeps aspect
        except Exception:  # noqa: BLE001 - draw a placeholder if a photo fails
            photo = Image.new("RGB", (col_w, cell_h), (60, 60, 60))
        canvas.paste(photo, (int(x), int(ytop)))

    # Footer / credit
    draw.text(
        (margin, footer_top),
        "Generated with Moodmark · Photos via Unsplash",
        font=footer_font,
        fill=muted,
    )

    buf = BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------


def get_key(name: str) -> str:
    """Resolve an API key from st.secrets, falling back to an environment variable.

    Keys are configured by you (the app owner) in .streamlit/secrets.toml — users
    of the app never need their own.
    """
    # Accessing st.secrets raises if no secrets.toml exists, so guard it.
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # noqa: BLE001 - missing or unparseable secrets file
        pass
    return os.environ.get(name, "")


st.set_page_config(page_title="Moodmark", page_icon="📚", layout="wide")

st.title("📚 Moodmark 🔖")
st.caption(
    "Turn any book into a visual mood board — its color palette, aesthetics, and curated "
    "photos, plus similar reads to explore."
)

# Global styling: center the content, outline each section as a minimal card,
# and the red recommendation links + lighter detail pop-up.
st.markdown(
    """
    <style>
    /* Center everything to a comfortable max width. */
    [data-testid="stMainBlockContainer"], .block-container {
        max-width: 1300px;
        margin: 0 auto;
    }
    /* Each output section is a minimal bordered card (no fill, rounded corners). */
    div[class*="st-key-card_"] {
        border: 1px solid rgba(0, 0, 0, 0.45);
        border-radius: 14px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 1.25rem;
    }
    /* Recommendation title buttons look like clickable red links. */
    div[class*="st-key-rec_"] button p {
        color: #c81e1e !important;
        text-decoration: underline !important;
        font-weight: 600;
    }
    /* Detail pop-up sub-card — a few shades lighter than the page background. */
    .st-key-detailcard {
        background-color: #e8b6e8;
        padding: 1rem 1.25rem;
        border-radius: 12px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

anthropic_key = get_key("ANTHROPIC_API_KEY")
unsplash_key = get_key("UNSPLASH_ACCESS_KEY")

# Input and button on one row; button right-aligned at the screen edge and
# bottom-aligned so it lines up with the input field (not its label).
input_col, button_col = st.columns([4, 1], vertical_alignment="bottom")
with input_col:
    book_title = st.text_input(
        "Book title",
        placeholder='e.g. "Scythe" by Neal Shusterman',
    )
with button_col:
    # Always clickable -- clicking captures whatever's typed, so no Enter needed.
    generate = st.button("✨ Generate mood board", type="primary", use_container_width=True)

# --- Compute on click ---
# Everything here runs only when the button is pressed. The results are stashed
# in st.session_state so they survive the rerun that any later widget (like the
# download button) triggers -- otherwise the whole board would disappear.
if generate:
    # Make sure something was actually typed.
    if not book_title.strip():
        st.warning("Type a book title first, then click Generate.")
        st.stop()
    # Validate keys before doing anything.
    if not anthropic_key:
        st.error("App is missing its ANTHROPIC_API_KEY — set it in .streamlit/secrets.toml.")
        st.stop()
    if not unsplash_key:
        st.error("App is missing its UNSPLASH_ACCESS_KEY — set it in .streamlit/secrets.toml.")
        st.stop()

    # Step 1: Claude designs the board.
    try:
        with st.spinner("Reading the book and choosing a palette..."):
            board = generate_mood_board(book_title, anthropic_key)
    except Exception as e:  # noqa: BLE001 - show the user a friendly message
        st.error(f"Something went wrong talking to Claude: {e}")
        st.stop()

    # Step 1b: look up real covers + publish year (best-effort, no API key) for
    # the main book and each recommendation (used by the detail panel).
    with st.spinner("Looking up the book..."):
        book_info = fetch_book_info(board.book_title, board.author)
        rec_info = [fetch_book_info(rec.title, rec.author) for rec in board.recommendations]

    # Step 2: fetch photos. One Unsplash request per query, so only fetch as many
    # queries as we need photos (each returns up to 3, leaving headroom for
    # backfill). This keeps us well under Unsplash's free tier of 50 req/hour.
    TARGET_PHOTOS = 6
    try:
        with st.spinner("Finding photos..."):
            candidates_by_query = []
            for query in board.image_queries[:TARGET_PHOTOS]:
                photos = search_unsplash(query, unsplash_key)
                for photo in photos:
                    photo["query"] = query
                candidates_by_query.append(photos)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 403:
            st.warning(
                "Hit Unsplash's hourly rate limit (50 requests/hour on the free Demo tier). "
                "Wait an hour and try again — or upgrade your Unsplash app to Production "
                "for a much higher limit."
            )
        else:
            st.error(f"Something went wrong fetching photos from Unsplash: {e}")
        st.stop()
    except Exception as e:  # noqa: BLE001
        st.error(f"Something went wrong fetching photos from Unsplash: {e}")
        st.stop()

    # Pick 6 strongest + most diverse: take each query's top-ranked photo first
    # (Unsplash sorts by relevance, so #1 is the best match, and spreading across
    # queries keeps the set visually varied), then loop back for second-best, etc.
    gallery = []
    rank = 0
    while len(gallery) < TARGET_PHOTOS and any(len(p) > rank for p in candidates_by_query):
        for photos in candidates_by_query:
            if rank < len(photos):
                gallery.append(photos[rank])
                if len(gallery) == TARGET_PHOTOS:
                    break
        rank += 1

    # Stash for rendering (and so it survives download-button reruns).
    st.session_state["board"] = board
    st.session_state["gallery"] = gallery
    st.session_state["book_info"] = book_info
    st.session_state["rec_info"] = rec_info
    # A fresh board closes any recommendation panel left open from a prior book.
    st.session_state.pop("selected_rec", None)


# --- Render whatever board is currently in session state ---
if "board" in st.session_state:
    board = st.session_state["board"]
    gallery = st.session_state["gallery"]

    # Show only the top 4 colors (the schema asks for 4, but slice to be safe).
    top_colors = board.palette[:4]

    # Header / vibe, with the real cover + publish year when Open Library has them.
    info = st.session_state.get("book_info") or {}
    cover_url = info.get("cover_url")
    year = info.get("year")

    with st.container(key="card_header"):
        if cover_url:
            cover_col, header = st.columns([1, 5], vertical_alignment="center")
            # Center the thumbnail within its own column (not pinned to the left edge).
            cover_col.markdown(
                f'<div style="text-align:center;"><img src="{cover_url}" width="110"></div>',
                unsafe_allow_html=True,
            )
        else:
            header = st  # no cover -> write straight into the card

        header.subheader(f"{board.book_title} — {board.author}")
        if year:
            header.caption(f"First published {year}")

        # A short essence-capturing quote, bold + italic, above the description.
        # (getattr guards old boards that predate this field.)
        essence_quote = getattr(board, "essence_quote", "")
        if essence_quote:
            header.markdown(f"***“{essence_quote}”***")

        header.write(board.overall_vibe)

    # Palette and Aesthetics side by side, inside one card.
    with st.container(key="card_identity"):
        palette_section, aesthetics_section = st.columns(2, gap="large")

        with palette_section:
            st.markdown("### 🎨 Color palette")
            for color in top_colors:
                # A color chip beside the name + hex, with the reasoning below.
                st.markdown(
                    f"""
                    <div style="display:flex;align-items:center;gap:12px;">
                        <div style="background:{color.hex};width:38px;height:38px;
                                    border-radius:8px;border:1px solid rgba(0,0,0,0.2);
                                    flex:none;"></div>
                        <div>
                            <div style="font-weight:600;line-height:1.1;">{color.name}</div>
                            <div style="font-family:monospace;font-size:0.85em;opacity:0.7;">{color.hex}</div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                st.caption(color.reason)

        with aesthetics_section:
            st.markdown("### 🏷️ Aesthetics")
            for aesthetic in board.aesthetics:
                st.markdown(f"**{aesthetic.keyword}** — {aesthetic.reason}")

    # The mood, in pictures — full width, but compact so it stays short.
    with st.container(key="card_pics"):
        if not gallery:
            st.markdown("### 🖼️ The mood, in pictures")
            st.info("No photos came back. Try a different book or tweak the title.")
        else:
            # Build the shareable image up front so the download button can sit in
            # the title row (it needs the PNG bytes ready before it renders).
            board_png = None
            try:
                with st.spinner("Composing a shareable image..."):
                    board_png = build_board_image(
                        board.book_title,
                        board.author,
                        tuple((c.hex, c.name) for c in top_colors),
                        tuple(p["download_url"] for p in gallery),
                    )
            except Exception as e:  # noqa: BLE001
                st.error(f"Couldn't build the downloadable image: {e}")

            # Title on the left, compact download button to the right.
            title_col, btn_col = st.columns([4, 1], vertical_alignment="bottom")
            title_col.markdown("### 🖼️ The mood, in pictures")
            if board_png is not None:
                file_stub = board.book_title.strip().lower().replace(" ", "_") or "book"
                btn_col.download_button(
                    "⬇️ Download",
                    data=board_png,
                    file_name=f"{file_stub}_moodboard.png",
                    mime="image/png",
                    type="primary",
                    use_container_width=True,
                )

            # One compact row of thumbnails keeps the vertical footprint small.
            grid = st.columns(len(gallery))
            for col, photo in zip(grid, gallery):
                with col:
                    st.image(photo["image_url"], use_container_width=True)
                    st.caption(
                        f"[{photo['photographer']}]({photo['photographer_url']}) · "
                        f"[Unsplash]({photo['link']})"
                    )

    # Recommendations (left) with a detail panel that opens beside them on click.
    with st.container(key="card_recs"):
        st.markdown("### ✨ If you liked the vibe")
        recommendations = getattr(board, "recommendations", []) or []
        rec_info = st.session_state.get("rec_info", [])

        sel = st.session_state.get("selected_rec")
        if sel is not None and sel >= len(recommendations):
            sel = None  # guard against a stale index from a previous board

        # Hint sits directly under the heading, only while nothing is open.
        if sel is None:
            st.caption("👈 Click a book title to explore its own vibe.")

        recs_col, detail_col = st.columns([3, 2], gap="large")

        with recs_col:
            if not recommendations:
                st.caption("No recommendations this time.")
            for i, rec in enumerate(recommendations):
                info = rec_info[i] if i < len(rec_info) else {}
                cover = info.get("cover_url")
                c_img, c_txt = st.columns([1, 5], vertical_alignment="center")
                if cover:
                    c_img.markdown(
                        f'<div style="text-align:center;"><img src="{cover}" width="76"></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    c_img.markdown(
                        "<div style='text-align:center;font-size:2.2rem;'>📚</div>",
                        unsafe_allow_html=True,
                    )
                with c_txt:
                    # Link-style clickable title opens this book in the detail panel.
                    if st.button(f"{i + 1}. {rec.title}", key=f"rec_{i}", type="tertiary"):
                        st.session_state["selected_rec"] = i
                        st.rerun()
                    rec_year = info.get("year")
                    st.caption(f"{rec.author}, {rec_year}" if rec_year else rec.author)
                    for reason in rec.why_similar[:3]:
                        st.markdown(f"- {reason}")
                st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

        with detail_col:
            if sel is not None:
                # Keyed container so the global CSS paints it as a lighter "card".
                with st.container(key="detailcard"):
                    rec = recommendations[sel]
                    info = rec_info[sel] if sel < len(rec_info) else {}
                    cover, year = info.get("cover_url"), info.get("year")

                    if st.button("✕ Close", key="close_rec"):
                        st.session_state.pop("selected_rec", None)
                        st.rerun()

                    if cover:
                        st.markdown(
                            f'<div style="text-align:center;"><img src="{cover}" width="130"></div>',
                            unsafe_allow_html=True,
                        )
                    st.markdown(f"**{rec.title}**")
                    st.caption(rec.author + (f" · {year}" if year else ""))

                    # Richer description + this book's palette, generated on demand (cached).
                    try:
                        with st.spinner("Reading this book's vibe..."):
                            detail = generate_rec_detail(rec.title, rec.author, anthropic_key)
                    except Exception:  # noqa: BLE001 - detail is a nice-to-have, never fatal
                        detail = None

                    # Richer description, falling back to the brief one from the main board.
                    st.write(detail.description if detail else rec.description)

                    if detail and detail.palette:
                        st.markdown("**Palette**")
                        for color in detail.palette[:3]:
                            # Swatch + name + hex, then the reason it fits this book.
                            st.markdown(
                                f"""
                                <div style="display:flex;align-items:center;gap:10px;">
                                    <div style="background:{color.hex};width:30px;height:30px;
                                                border-radius:6px;border:1px solid rgba(0,0,0,0.25);
                                                flex:none;"></div>
                                    <div>
                                        <div style="font-weight:600;line-height:1.1;">{color.name}</div>
                                        <div style="font-family:monospace;font-size:0.8em;opacity:0.7;">{color.hex}</div>
                                    </div>
                                </div>
                                """,
                                unsafe_allow_html=True,
                            )
                            st.caption(color.reason)
