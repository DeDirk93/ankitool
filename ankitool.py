#!/usr/bin/env python3
"""CLI tool to manage a Japanese Anki vocabulary deck outside of Anki."""

import argparse
import csv
import json
import sqlite3
import sys
import tempfile
import unicodedata
import zipfile
from collections import defaultdict
from pathlib import Path

try:
    import zstandard
    _HAVE_ZSTD = True
except ImportError:
    _HAVE_ZSTD = False

VERSION = "1.0.0"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
FIELD_SEP = "\x1f"
EXCLUDE_FIELDS = {"Audio"}


def _die(msg: str) -> None:
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def _decompress_anki21b(raw: bytes) -> bytes:
    if not _HAVE_ZSTD:
        _die(
            "collection.anki21b is zstd-compressed. "
            "Install zstandard: pip install zstandard"
        )
    try:
        import io
        dctx = zstandard.ZstdDecompressor()
        return dctx.stream_reader(io.BytesIO(raw)).read()
    except Exception as e:
        _die(f"Failed to decompress collection.anki21b: {e}")


def open_apkg(apkg_path: str) -> tuple[sqlite3.Connection, tempfile.TemporaryDirectory]:
    """Extract .apkg, decompress if needed, return (sqlite3 connection, TemporaryDirectory)."""
    path = Path(apkg_path)
    if not path.exists():
        _die(f"File not found: {apkg_path}")
    if path.suffix.lower() != ".apkg":
        _die(f"Not an .apkg file: {apkg_path}")

    tmp = tempfile.TemporaryDirectory()
    try:
        with zipfile.ZipFile(apkg_path) as zf:
            zf.extractall(tmp.name)
    except zipfile.BadZipFile:
        tmp.cleanup()
        _die(f"Not a valid .apkg (bad zip): {apkg_path}")

    tmp_path = Path(tmp.name)
    anki21b = tmp_path / "collection.anki21b"
    anki2 = tmp_path / "collection.anki2"

    if anki21b.exists():
        raw = anki21b.read_bytes()
        if raw[:4] == ZSTD_MAGIC:
            db_path = tmp_path / "collection_dec.db"
            db_path.write_bytes(_decompress_anki21b(raw))
        else:
            db_path = anki21b
    elif anki2.exists():
        db_path = anki2
    else:
        tmp.cleanup()
        _die(f"No collection database found inside {apkg_path}")

    try:
        conn = sqlite3.connect(str(db_path))
        conn.create_collation("unicase", lambda a, b: (a.lower() > b.lower()) - (a.lower() < b.lower()))
        conn.execute("SELECT 1 FROM col LIMIT 1")
    except sqlite3.DatabaseError as e:
        tmp.cleanup()
        _die(f"Cannot read collection database: {e}")

    return conn, tmp


def _load_field_map(conn: sqlite3.Connection) -> dict[int, list[str]]:
    """Return {notetype_id: [field_name, ...]} ordered by ord."""
    try:
        rows = conn.execute(
            "SELECT ntid, name FROM fields ORDER BY ntid, ord"
        ).fetchall()
        if rows:
            result: dict[int, list[str]] = {}
            for ntid, name in rows:
                result.setdefault(ntid, []).append(name)
            return result
    except sqlite3.OperationalError:
        pass

    # Legacy schema: models JSON in col table
    try:
        models_json = conn.execute("SELECT models FROM col").fetchone()[0]
        models = json.loads(models_json)
        return {
            int(mid): [f["name"] for f in sorted(m["flds"], key=lambda f: f["ord"])]
            for mid, m in models.items()
        }
    except Exception as e:
        _die(f"Could not load field definitions from database: {e}")


def _target_notetype(field_map: dict[int, list[str]]) -> tuple[int, list[str]]:
    for ntid, names in field_map.items():
        if "Japanese" in names:
            return ntid, names
    _die("No notetype with a 'Japanese' field found in deck")


def _open_csv(path: "str | Path") -> "csv.writer":
    """Open a CSV writer with UTF-8-BOM encoding for Numbers compatibility."""
    return open(path, "w", newline="", encoding="utf-8-sig")


def cmd_export(args: argparse.Namespace) -> None:
    conn, tmp = open_apkg(args.deck)
    try:
        field_map = _load_field_map(conn)
        target_mid, names = _target_notetype(field_map)
        export_fields = [n for n in names if n not in EXCLUDE_FIELDS]

        out_path = Path(args.deck).with_suffix(".csv")
        rows_written = 0

        with _open_csv(out_path) as f:
            writer = csv.writer(f)
            writer.writerow(export_fields)
            for mid, flds in conn.execute("SELECT mid, flds FROM notes"):
                if mid != target_mid:
                    continue
                parts = flds.split(FIELD_SEP)
                row = [
                    parts[i] if i < len(parts) else ""
                    for i, name in enumerate(names)
                    if name in export_fields
                ]
                writer.writerow(row)
                rows_written += 1

        print(f"Exported {rows_written} notes → {out_path}")
    finally:
        conn.close()
        tmp.cleanup()


def _normalize(s: str) -> str:
    return unicodedata.normalize("NFC", s).strip()


def _first_reading_token(reading: str) -> str:
    return reading.strip().split()[0] if reading.strip() else ""


def cmd_dupes(args: argparse.Namespace) -> None:
    conn, tmp = open_apkg(args.deck)
    try:
        field_map = _load_field_map(conn)
        target_mid, names = _target_notetype(field_map)

        jp_idx = names.index("Japanese")
        reading_idx = names.index("Reading") if "Reading" in names else None

        notes: list[tuple[int, str, str]] = []
        for note_id, mid, flds in conn.execute("SELECT id, mid, flds FROM notes"):
            if mid != target_mid:
                continue
            parts = flds.split(FIELD_SEP)
            jp = parts[jp_idx] if jp_idx < len(parts) else ""
            reading = parts[reading_idx] if reading_idx is not None and reading_idx < len(parts) else ""
            notes.append((note_id, jp, reading))

        # Exact duplicates on raw Japanese field
        by_jp: dict[str, list] = defaultdict(list)
        for note in notes:
            by_jp[note[1]].append(note)
        exact_groups = {k: v for k, v in by_jp.items() if len(v) > 1}

        # Near-dupes: NFC-normalized Japanese
        by_norm: dict[str, list] = defaultdict(list)
        for note in notes:
            by_norm[_normalize(note[1])].append(note)
        norm_groups = {
            k: v for k, v in by_norm.items()
            if len(v) > 1 and k not in exact_groups
        }

        # Near-dupes: matching first reading token
        by_reading: dict[str, list] = defaultdict(list)
        for note in notes:
            key = _normalize(_first_reading_token(note[2]))
            if key:
                by_reading[key].append(note)

        reading_groups = {
            k: v for k, v in by_reading.items()
            if len(v) > 1
            and not any(
                _normalize(n[1]) in exact_groups or _normalize(n[1]) in by_norm
                and len(by_norm[_normalize(n[1])]) > 1
                and all(_normalize(x[1]) == _normalize(n[1]) for x in v)
                for n in v
            )
        }

        total = len(exact_groups) + len(norm_groups) + len(reading_groups)
        if total == 0:
            print("No duplicates found.")
            return

        def _print_group(label: str, groups: dict) -> None:
            for key, members in sorted(groups.items()):
                print(f"\n  [{label}] key: {key!r}")
                for note_id, jp, reading in members:
                    print(f"    id={note_id}  Japanese={jp!r}  Reading={reading!r}")

        print(f"=== Duplicate report: {len(notes)} notes scanned ===")
        print(f"  Exact duplicates (Japanese field):   {len(exact_groups)} group(s)")
        print(f"  Near-dupes (NFC normalized):         {len(norm_groups)} group(s)")
        print(f"  Near-dupes (matching first reading): {len(reading_groups)} group(s)")

        if exact_groups:
            print("\n--- EXACT DUPLICATES ---")
            _print_group("exact", exact_groups)
        if norm_groups:
            print("\n--- NEAR-DUPES: normalized Japanese ---")
            _print_group("norm", norm_groups)
        if reading_groups:
            print("\n--- NEAR-DUPES: same first reading token ---")
            _print_group("reading", reading_groups)

    finally:
        conn.close()
        tmp.cleanup()


def cmd_mature(args: argparse.Namespace) -> None:
    conn, tmp = open_apkg(args.deck)
    try:
        field_map = _load_field_map(conn)
        target_mid, names = _target_notetype(field_map)

        jp_idx = names.index("Japanese")
        en_idx = names.index("English") if "English" in names else None
        reading_idx = names.index("Reading") if "Reading" in names else None

        rows = conn.execute(
            """
            SELECT DISTINCT n.id, n.flds
            FROM notes n
            JOIN cards c ON c.nid = n.id
            WHERE n.mid = ? AND c.ivl >= 21
            """,
            (target_mid,),
        ).fetchall()

        out_path = Path(args.deck).stem + "-mature-words.csv"
        with _open_csv(out_path) as f:
            writer = csv.writer(f)
            writer.writerow(["Japanese", "English", "Reading"])
            for _, flds in rows:
                parts = flds.split(FIELD_SEP)
                writer.writerow([
                    parts[jp_idx] if jp_idx < len(parts) else "",
                    parts[en_idx] if en_idx is not None and en_idx < len(parts) else "",
                    parts[reading_idx] if reading_idx is not None and reading_idx < len(parts) else "",
                ])

        print(f"Exported {len(rows)} mature notes → {out_path}")
    finally:
        conn.close()
        tmp.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ankitool",
        description="Manage a Japanese Anki vocabulary deck outside of Anki.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.required = True

    p_export = sub.add_parser("export", help="Export deck to <deck>.csv")
    p_export.add_argument("deck", metavar="deck.apkg")
    p_export.set_defaults(func=cmd_export)

    p_dupes = sub.add_parser("dupes", help="Report duplicate notes")
    p_dupes.add_argument("deck", metavar="deck.apkg")
    p_dupes.set_defaults(func=cmd_dupes)

    p_mature = sub.add_parser("mature", help="Export mature words (ivl >= 21) to <deck>-mature-words.csv")
    p_mature.add_argument("deck", metavar="deck.apkg")
    p_mature.set_defaults(func=cmd_mature)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
