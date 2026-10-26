#!/usr/bin/env python3
"""
Extract NPC greetings (and optionally topic texts) from an MMMerge install
into a CSV that generate_voices.py turns into audio.

Read-only on game files. Usage:

    python extract_texts.py --game "C:/path/to/MM8" --out greetings.csv
    python extract_texts.py --game ... --out texts.csv --topics

Every row carries a `key`: CRC32 of the normalized text. The Lua side
(Scripts/General/zzzMaw_NPCVoices.lua) computes the same key from the text
the game is about to show, so audio files are found by text, not by table
index. A text MAW changed later simply has no file and stays silent.
Keep normalize() and the Lua normalize() identical.
"""

import argparse
import csv
import re
import struct
import sys
import zlib
from pathlib import Path

TEXT_ENCODING = "cp1252"  # what the game's text tables are stored in

# --------------------------------------------------------------------------
# key: must match normalize()/crc32 in zzzMaw_NPCVoices.lua byte for byte

_WS = re.compile(rb"[ \t\r\n\v\f]+")


def normalize(raw: bytes) -> bytes:
    """Drop quotes, collapse whitespace, trim, lowercase ASCII only."""
    raw = raw.replace(b'"', b"")
    raw = _WS.sub(b" ", raw).strip(b" ")
    return raw.lower()  # bytes.lower() only touches A-Z


def text_key(raw: bytes) -> str:
    return f"{zlib.crc32(normalize(raw)) & 0xFFFFFFFF:08x}"


# --------------------------------------------------------------------------
# LOD reader (MM8 "MMVIII" layout)

def read_lod(path: Path) -> dict:
    """Return {lowercase entry name: bytes} for a text LOD."""
    data = path.read_bytes()
    if data[:4] != b"LOD\0":
        raise ValueError(f"{path}: not a LOD file")
    _name, folder_off, _size, _unk, count = struct.unpack_from("<16sIIII", data, 0x100)
    entries = {}
    for i in range(count):
        name, off, size, _unk = struct.unpack_from("<64sIII", data, folder_off + i * 0x4C)
        name = name.split(b"\0", 1)[0].decode("latin-1")
        raw = data[folder_off + off: folder_off + off + size]
        unpacked_size = struct.unpack_from("<I", raw, 88)[0]
        entries[name.lower()] = zlib.decompress(raw[96:]) if unpacked_size else raw[96:]
    return entries


# --------------------------------------------------------------------------
# Tables: tab separated, rows end with CRLF, Excel-style quoted cells.
# Line breaks inside a cell are a bare LF, quoted or not.

def parse_table(raw: bytes) -> list:
    rows, row, cell = [], [], bytearray()
    i, n = 0, len(raw)
    at_cell_start, quoted = True, False
    while i < n:
        c = raw[i]
        if quoted:
            if c == 0x22:  # "
                if raw[i + 1: i + 2] == b'"':
                    cell += b'"'
                    i += 2
                    continue
                nxt = raw[i + 1: i + 2]
                if nxt in (b"\t", b"\r", b""):
                    quoted = False
                else:
                    cell += b'"'  # stray quote inside a quoted cell: keep it
            else:
                cell += bytes((c,))
            i += 1
            continue
        if at_cell_start and c == 0x22:
            quoted, at_cell_start = True, False
            i += 1
            continue
        at_cell_start = False
        if c == 0x09:
            row.append(bytes(cell))
            cell, at_cell_start = bytearray(), True
        elif c == 0x0D and raw[i + 1: i + 2] == b"\n":
            row.append(bytes(cell))
            rows.append(row)
            row, cell, at_cell_start = [], bytearray(), True
            i += 1
        else:
            cell += bytes((c,))
        i += 1
    if cell or row:
        row.append(bytes(cell))
        rows.append(row)
    return rows


def cell(row, i) -> bytes:
    return row[i] if i < len(row) else b""


def as_int(b: bytes):
    try:
        return int(b.strip() or b"x")
    except ValueError:
        return None


def decode(b: bytes) -> str:
    return b.decode(TEXT_ENCODING, errors="replace")


def speech_text(raw: bytes) -> str:
    """Text as it should be spoken: no quote marks, one line."""
    return _WS.sub(b" ", raw.replace(b'"', b"")).strip().decode(TEXT_ENCODING, errors="replace")


# --------------------------------------------------------------------------
# Gender guess for picking a voice. Special NPCs have no sex field in
# NPCData.txt, so this is a heuristic; the CSV can be edited by hand.

def load_gender_hints(tables_dir: Path):
    by_pic, by_name = {}, {}
    portraits = tables_dir / "Character portraits.txt"
    if portraits.exists():
        rows = parse_table(portraits.read_bytes())
        head = [decode(c).strip().lower() for c in rows[0]]
        if "def sex" in head and "npcpic" in head:
            sex_i, pic_i = head.index("def sex"), head.index("npcpic")
            for r in rows[1:]:
                pic, sex = as_int(cell(r, pic_i)), as_int(cell(r, sex_i))
                if pic and sex in (0, 1):
                    by_pic[pic] = "m" if sex == 0 else "f"
    names = tables_dir / "NPC names.txt"
    if names.exists():
        for r in parse_table(names.read_bytes())[1:]:
            for col, g in ((0, "m"), (1, "f")):
                name = decode(cell(r, col)).strip().lower()
                if name:
                    by_name.setdefault(name, g)
    return by_pic, by_name


def guess_gender(name: str, pic, by_pic, by_name) -> str:
    if pic in by_pic:
        return by_pic[pic]
    first = name.strip().split(" ")[0].lower() if name.strip() else ""
    return by_name.get(first, "?")


# --------------------------------------------------------------------------

def load_npcs(table: bytes):
    """NPCData.txt: two header rows; # Name Pic A B C 2DLoc Prof Greet Join EvA..EvF Notes."""
    npcs = {}
    for r in parse_table(table)[2:]:
        idx = as_int(cell(r, 0))
        if idx is None:
            continue
        npcs[idx] = {
            "name": decode(cell(r, 1)).strip(),
            "pic": as_int(cell(r, 2)) or 0,
            "greet": as_int(cell(r, 8)) or 0,
            "events": [e for e in (as_int(cell(r, k)) for k in range(10, 16)) if e],
        }
    return npcs


def global_set_messages(evt: bytes) -> dict:
    """{event id: [NPCText index, ...]} for every SetMessage (0x1E) in Global.EVT."""
    result, p = {}, 0
    while p < len(evt):
        size = evt[p]
        rec = evt[p + 1: p + 1 + size]
        p += 1 + size
        if len(rec) < 8:
            continue
        evt_id, _seq, cmd = struct.unpack_from("<HBB", rec, 0)
        if cmd == 0x1E:
            result.setdefault(evt_id, []).append(struct.unpack_from("<i", rec, 4)[0])
    return result


class Collector:
    """One output row per distinct key; NPCs sharing a text are merged."""

    def __init__(self):
        self.rows = {}

    def add(self, raw: bytes, kind, source, npcs, gender_of):
        if not normalize(raw):
            return
        key = text_key(raw)
        row = self.rows.get(key)
        if row is None:
            row = self.rows[key] = {
                "key": key, "kind": kind, "source": source,
                "npc_ids": [], "npc_names": [], "pics": [], "genders": [],
                "text": speech_text(raw),
            }
        for idx, npc in npcs:
            if idx in row["npc_ids"]:
                continue
            row["npc_ids"].append(idx)
            row["npc_names"].append(npc["name"])
            row["pics"].append(npc["pic"])
            row["genders"].append(gender_of(npc))

    def write(self, out: Path):
        fields = ["key", "kind", "source", "gender", "npc_ids", "npc_names",
                  "pics", "chars", "voice", "text"]
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for row in self.rows.values():
                known = [g for g in row["genders"] if g != "?"]
                gender = known[0] if known and all(g == known[0] for g in known) else ("mixed" if known else "?")
                w.writerow({
                    "key": row["key"], "kind": row["kind"], "source": row["source"],
                    "gender": gender,
                    "npc_ids": " ".join(map(str, row["npc_ids"])),
                    "npc_names": "; ".join(row["npc_names"]),
                    "pics": " ".join(map(str, row["pics"])),
                    "chars": len(row["text"]),
                    "voice": "",
                    "text": row["text"],
                })


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--game", required=True, type=Path, help="MM8 install with MMMerge")
    ap.add_argument("--lod", default="Data/mmmerge.T.lod", help="text LOD, relative to --game")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--topics", action="store_true",
                    help="also extract NPCText messages that each NPC's topics show (Global.EVT SetMessage)")
    args = ap.parse_args(argv)

    lod = read_lod(args.game / args.lod)
    greets = parse_table(lod["npcgreet.txt"])
    npcs = load_npcs(lod["npcdata.txt"])
    by_pic, by_name = load_gender_hints(args.game / "Data" / "Tables")
    gender_of = lambda npc: guess_gender(npc["name"], npc["pic"], by_pic, by_name)

    owners, by_name = {}, {}
    for idx, npc in npcs.items():
        if npc["greet"]:
            owners.setdefault(npc["greet"], []).append((idx, npc))
        by_name.setdefault(npc["name"].lower(), (idx, npc))

    out = Collector()
    for r in greets[1:]:
        gid = as_int(cell(r, 0))
        if gid is None:
            continue
        # greetings that scripts assign later (SetNPCGreeting) have no NPCData
        # row pointing at them; the Owner column still names the speaker
        speakers = owners.get(gid) or [by_name[n] for n in [decode(cell(r, 4)).strip().lower()] if n in by_name]
        for col in (1, 2):
            out.add(cell(r, col), "greeting", f"NPCGreet {gid}.{col}", speakers, gender_of)
    n_greet = len(out.rows)

    if args.topics:
        texts = {as_int(cell(r, 0)): cell(r, 1) for r in parse_table(lod["npctext.txt"])[1:]}
        messages = global_set_messages(lod["global.evt"])
        for idx, npc in npcs.items():
            for ev in npc["events"]:
                for t in messages.get(ev, []):
                    if t in texts:
                        out.add(texts[t], "topic", f"NPCText {t} (event {ev})", [(idx, npc)], gender_of)

    out.write(args.out)
    total_chars = sum(len(r["text"]) for r in out.rows.values())
    print(f"{len(out.rows)} distinct texts ({n_greet} greetings, {len(out.rows) - n_greet} topic texts), "
          f"{total_chars} characters -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
