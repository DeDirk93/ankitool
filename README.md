# ankitool

A command-line tool to work with Japanese Anki vocabulary decks outside of Anki.

## Requirements

- Python 3.9+
- [zstandard](https://pypi.org/project/zstandard/) — required to read modern `.apkg` files

```
pip install zstandard
```

## Usage

```
python ankitool.py <command> <deck.apkg>
```

### Commands

| Command | Description | Output |
|---------|-------------|--------|
| `export` | Export all notes to CSV | `<deck>.csv` |
| `dupes` | Report exact and near-duplicate notes | stdout |
| `mature` | Export mature cards (interval ≥ 21 days) | `<deck>-mature-words.csv` |

### Examples

```bash
# Export entire deck to CSV
python ankitool.py export MyDeck.apkg

# Check for duplicate entries
python ankitool.py dupes MyDeck.apkg

# Export only well-learned (mature) cards
python ankitool.py mature MyDeck.apkg
```

## Deck format

ankitool expects a Basic note type with these fields: **Japanese**, **English**, **Reading**, **Part of Speech**, **Audio**.

The Japanese field is used as the unique key. CSV output uses UTF-8 with BOM so macOS Numbers opens it correctly without manual encoding setup.

## Duplicate detection

The `dupes` command runs three tiers of checks, reported separately:

1. **Exact** — identical raw Japanese field value
2. **Normalized** — identical after NFC unicode normalization and whitespace trimming
3. **Reading** — identical first token in the Reading field (catches kanji/kana variants and homophones)

## Version

1.0.0
