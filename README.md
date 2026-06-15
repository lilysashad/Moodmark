# 📚 Book Mood Board Generator

Type in any book title and get back a visual mood board: the story's overall vibe,
a color palette (with the reasoning behind each color), aesthetic keywords, and a
grid of real photos that capture the feeling of the book.

## How it works

Two steps behind a [Streamlit](https://streamlit.io) interface:

1. **Claude** (`claude-opus-4-8`) "reads" the book and returns a structured mood-board
   concept — overall vibe, hex color palette, aesthetic tags, and a set of image
   search queries.
2. Those queries are sent to the **Unsplash** photo API, which returns real
   Pinterest-style images. Each photo credits its photographer (an Unsplash
   requirement).

Claude only produces text, so the visuals come from Unsplash — that split is the
whole architecture.

## Setup

### 1. Get two API keys (both free to start)

- **Anthropic** (for Claude): https://console.anthropic.com
- **Unsplash** (for photos): https://unsplash.com/developers — register an app and
  copy the **Access Key**.

### 2. Install dependencies

```powershell
cd book-mood-board
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 3. Add your keys

Either paste them into the app's sidebar each time, **or** store them once:
copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in
your keys. (That file is git-ignored so your keys stay private.)

### 4. Run it

```powershell
streamlit run app.py
```

Your browser opens to the app. Type a book title (try `Scythe`) and hit
**Generate mood board**.

## Notes & costs

- Each generation makes **one Claude call** plus a handful of Unsplash searches.
  Results are cached per book title, so re-typing the same book is free.
- Unsplash's free Demo tier allows 50 requests/hour — plenty for personal use.

## Ideas for later

- Let users pick a specific edition or paste a blurb for more accurate vibes.
- Download the mood board as an image or PDF.
- Save favorite boards to revisit.
- Generate original images (instead of stock photos) by adding an image-generation API.
