# ankitool

CLI tool to manage a Japanese Anki vocabulary deck outside of Anki.

## Stack
- Python 3.9+, single file: `ankitool.py`
- stdlib: zipfile, sqlite3, csv, json, unicodedata
- One external dependency: `zstandard` (pip install zstandard) — required to decompress collection.anki21b
- macOS / Apple Silicon

## Anki Deck Structure
- Note type: Basic (notetype name in `notetypes` table)
- Fields (in order): Japanese, English, Reading, Part of Speech, Audio
- Japanese field is the unique key — never modify it during updates
- Audio field contains [sound:filename.mp3] — exclude from all exports
- .apkg is a zip containing collection.anki21b (preferred) and/or collection.anki2 (SQLite)
- collection.anki21b: zstd-compressed SQLite (magic bytes 28 b5 2f fd). Decompress before opening.
- collection.anki2: plain SQLite, but in modern exports it's a stub with a "please update Anki" note — ignore it if anki21b is present
- notes.flds column: all fields joined with \x1f (unit separator, chr(31))
- cards.ivl: interval in days. Mature = ivl >= 21

## Modern schema (anki21b)
- Field definitions live in the `fields` table: columns ntid (notetype id), ord, name
- Notetype list in `notetypes` table: columns id, name
- Legacy schema had field defs as JSON in col.models — handle both for compatibility
- The deck has additional built-in notetypes (Front/Back etc.) alongside the Japanese Basic notetype; filter by notetypes that have a "Japanese" field
- The anki21b zstd frame has no content-size header — use streaming decompression (`stream_reader`) not `decompress()`
- The `fields` table uses a `unicase` collation; register a dummy one on the connection or queries will fail: `conn.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))`

## Commands
```
ankitool export <deck.apkg>   → <deck>.csv (Japanese, English, Reading, Part of Speech)
ankitool dupes <deck.apkg>    → stdout report: exact + near-duplicates on Japanese field
ankitool mature <deck.apkg>   → <deck>-mature-words.csv (cards with ivl >= 21)
ankitool --version            → print version and exit
```

## Output format
- CSV, UTF-8 with BOM (utf-8-sig) — Numbers on macOS opens Japanese characters correctly without manual encoding selection
- Row 1: header
- Reimport to Anki: export from Numbers as tab-separated .txt, import manually
- Audio field is always excluded from CSV output

## Error handling
- Missing file → Error: File not found
- Wrong extension (not .apkg) → Error: Not an .apkg file
- Bad zip → Error: Not a valid .apkg (bad zip)
- Unreadable SQLite → Error: Cannot read collection database
- Missing zstandard → Error with install instructions
- All errors print to stderr and exit 1

## Near-duplicate detection (dupes command)
Three tiers, reported separately:
1. Exact: identical raw Japanese field value
2. Normalized: identical after NFC unicode normalization + whitespace strip
3. Reading: identical first whitespace-separated token in the Reading field (catches kanji/kana variants and homophones)
Groups already caught by a higher tier are excluded from lower tiers.

## Version
1.0.0 — all three commands implemented and tested against a real deck (1534 notes, 1056 mature).

## Out of scope
- Writing back to .apkg directly
- Audio handling
- GUID generation