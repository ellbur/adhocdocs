"""Write Outlook .msg and .pst files, for the test corpus.

Nothing else in Python writes these, and the corpus holds only original
documents, so the corpus's Outlook files are made here. These are small,
deterministic writers from the published specifications (MS-CFB, MS-OXMSG,
MS-PST, MS-OXRTFCP, MS-OXRTFEX), not general-purpose ones: a Unicode PST
with no encryption, single-level heaps, and enough of the store, folder
and table structure for Outlook-format readers (libpff, which adhocdocs
uses to read PSTs) to find every folder, message, recipient and
attachment.

A message is described by a :class:`Msg`: MAPI properties as
``{prop_id: (prop_type, value)}``, recipients as property dicts, and
attachments as :class:`Att`.
"""

from __future__ import annotations

import datetime as dt
import re
import struct
import zlib
from dataclasses import dataclass, field

# Property types
I2, LONG, BOOL, I8, STR, BIN, TIME, OBJ, CLSID = 0x0002, 0x0003, 0x000B, 0x0014, 0x001F, 0x0102, 0x0040, 0x000D, 0x0048
FIXED_SIZE = {I2: 2, LONG: 4, BOOL: 1, I8: 8, TIME: 8, 0x0004: 4, 0x0005: 8, 0x0006: 8, 0x0007: 8}


@dataclass
class Att:
    props: dict
    data: bytes | None = None
    message: "Msg | None" = None


@dataclass
class Msg:
    props: dict
    recipients: list[dict] = field(default_factory=list)
    attachments: list[Att] = field(default_factory=list)


def filetime(t: dt.datetime) -> int:
    return int((t - dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc)).total_seconds() * 10_000_000)


def encode(ptype: int, value) -> bytes:
    if ptype == STR:
        return value.encode("utf-16-le")
    if ptype in (BIN, CLSID):
        return bytes(value)
    if ptype == LONG:
        return struct.pack("<i", value) if value < 2**31 else struct.pack("<I", value)
    if ptype == I2:
        return struct.pack("<h", value)
    if ptype == BOOL:
        return b"\x01" if value else b"\x00"
    if ptype == I8:
        return struct.pack("<q", value)
    if ptype == TIME:
        return struct.pack("<Q", filetime(value) if isinstance(value, dt.datetime) else value)
    raise ValueError(f"can't encode property type {ptype:#x}")


def crc(data: bytes) -> int:
    """The CRC of MS-PST and MS-OXRTFCP: CRC-32 with no inversion."""
    return zlib.crc32(data, 0xFFFFFFFF) ^ 0xFFFFFFFF


# ================================================================ RTF

PREBUF = (b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern "
          b"\\fscript \\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0"
          b"\\blue0\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx")


def compress_rtf(raw: bytes) -> bytes:
    """PidTagRtfCompressed: LZFu, greedy longest match (MS-OXRTFCP)."""
    window = bytearray(4096)
    window[:len(PREBUF)] = PREBUF
    wpos = len(PREBUF)
    out = bytearray()
    i = 0
    done = False
    while not done:
        control, chunk = 0, bytearray()
        for bit in range(8):
            if i >= len(raw):
                chunk += struct.pack(">H", wpos << 4)     # the end: a reference to the write position
                control |= 1 << bit
                done = True
                break
            best_len, best_off = 0, 0
            doubled = bytes(window) * 2
            for length in range(min(17, len(raw) - i), 1, -1):
                start = 0
                while True:
                    k = doubled.find(raw[i:i + length], start)
                    if k < 0:
                        break
                    off = k % 4096
                    # never read what this reference is about to write, nor point at the write position
                    span = {(off + j) % 4096 for j in range(length)}
                    if off != wpos and not span & {(wpos + j) % 4096 for j in range(length)}:
                        best_len, best_off = length, off
                        break
                    start = k + 1
                if best_len:
                    break
            if best_len >= 2:
                chunk += struct.pack(">H", (best_off << 4) | (best_len - 2))
                control |= 1 << bit
                seq = raw[i:i + best_len]
                i += best_len
            else:
                seq = raw[i:i + 1]
                chunk += seq
                i += 1
            for b in seq:
                window[wpos] = b
                wpos = (wpos + 1) % 4096
        out.append(control)
        out += chunk
    body = bytes(out)
    return struct.pack("<IIII", len(body) + 12, len(raw), 0x75465A4C, crc(body)) + body


def _rtf_escape(s: str) -> str:
    out = []
    for c in s:
        if c in "\\{}":
            out.append("\\" + c)
        elif c == "\n":
            out.append("\\par\n")
        elif ord(c) > 127:
            n = ord(c)
            out.append(f"\\u{n if n < 32768 else n - 65536}?")
        else:
            out.append(c)
    return "".join(out)


def html_to_rtf(html: str) -> bytes:
    """HTML encapsulated in RTF, as Outlook stores HTML mail (MS-OXRTFEX):
    tags in ``{\\*\\htmltag}`` groups, text as RTF text."""
    parts = ["{\\rtf1\\ansi\\ansicpg1252\\fromhtml1 \\deff0{\\fonttbl{\\f0\\fswiss Arial;}}\n"]
    for tag, text in re.findall(r"(<[^>]*>)|([^<]+)", html):
        if tag:
            parts.append("{\\*\\htmltag64 " + _rtf_escape(tag) + "}")
        else:
            parts.append(_rtf_escape(text))
    parts.append("}")
    return "".join(parts).encode("ascii")


# ================================================================ compound files (MS-CFB)

FREESECT, ENDOFCHAIN, FATSECT, NOSTREAM = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFF


class Storage:
    def __init__(self, name: str):
        self.name = name
        self.children: dict[str, "Storage | bytes"] = {}

    def storage(self, name: str) -> "Storage":
        s = self.children.setdefault(name, Storage(name))
        assert isinstance(s, Storage)
        return s

    def stream(self, name: str, data: bytes):
        self.children[name] = data


def cfb(root: Storage) -> bytes:
    """A version 3 compound file (512-byte sectors) holding root's tree."""
    entries = []          # [name, type, children-list, data]

    def add(name, node):
        idx = len(entries)
        if isinstance(node, Storage):
            entries.append([name, 1, [], None])
            kids = [add(n, c) for n, c in node.children.items()]
            entries[idx][2] = kids
        else:
            entries.append([name, 2, [], node])
        return idx

    add("Root Entry", root)
    entries[0][1] = 5

    # Streams under 4096 bytes go in the mini stream, in 64-byte sectors.
    mini = bytearray()
    minifat = []
    big = []
    start = {}
    for i, (name, typ, _, data) in enumerate(entries):
        if typ != 2:
            continue
        if len(data) == 0:
            start[i] = ENDOFCHAIN
        elif len(data) < 4096:
            first = len(mini) // 64
            n = (len(data) + 63) // 64
            minifat += [first + k + 1 for k in range(n - 1)] + [ENDOFCHAIN]
            mini += data + b"\x00" * (n * 64 - len(data))
            start[i] = ("mini", first)
        else:
            big.append(i)

    def sectors(n):
        return (n + 511) // 512

    n_dir = sectors(len(entries) * 128)
    n_minifat = sectors(len(minifat) * 4)
    n_mini = sectors(len(mini))
    n_big = sum(sectors(len(entries[i][3])) for i in big)
    n_fat = 1
    while n_fat * 128 < n_fat + n_dir + n_minifat + n_mini + n_big:
        n_fat += 1
    assert n_fat <= 109
    fat = [FATSECT] * n_fat
    sec = n_fat

    def chain(n):
        nonlocal sec
        first = sec
        fat.extend(list(range(sec + 1, sec + n)) + [ENDOFCHAIN])
        sec += n
        return first

    dir_start = chain(n_dir)
    minifat_start = chain(n_minifat) if n_minifat else ENDOFCHAIN
    mini_start = chain(n_mini) if n_mini else ENDOFCHAIN
    for i in big:
        start[i] = chain(sectors(len(entries[i][3])))
    fat += [FREESECT] * (n_fat * 128 - len(fat))

    # Directory: each storage's children as a balanced binary tree, all black.
    left = [NOSTREAM] * len(entries)
    right = [NOSTREAM] * len(entries)
    child = [NOSTREAM] * len(entries)

    def tree(ids):
        if not ids:
            return NOSTREAM
        ids = sorted(ids, key=lambda j: (len(entries[j][0]), entries[j][0].upper()))
        mid = len(ids) // 2
        left[ids[mid]] = tree(ids[:mid])
        right[ids[mid]] = tree(ids[mid + 1:])
        return ids[mid]

    for i, (_, typ, kids, _) in enumerate(entries):
        if typ in (1, 5):
            child[i] = tree(kids)
    d = bytearray()
    for i, (name, typ, _, data) in enumerate(entries):
        nm = name.encode("utf-16-le") + b"\x00\x00"
        assert len(nm) <= 64
        if typ == 5:
            st, size = mini_start, len(mini)
        elif typ == 2:
            s = start[i]
            st = s[1] if isinstance(s, tuple) else s
            size = len(data)
        else:
            st, size = 0, 0
        d += nm.ljust(64, b"\x00") + struct.pack("<HBBIII16sIQQIQ", len(nm), typ, 1, left[i], right[i], child[i],
                                                 b"\x00" * 16, 0, 0, 0, st, size)
    d += b"\x00" * (n_dir * 512 - len(d))
    for k in range(len(d) // 128):     # unused entries: no siblings, no child
        if k >= len(entries):
            struct.pack_into("<III", d, k * 128 + 68, NOSTREAM, NOSTREAM, NOSTREAM)

    header = bytearray(512)
    struct.pack_into("<8s16sHHHHH6sIIIIIIIII", header, 0, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"\x00" * 16,
                     0x3E, 3, 0xFFFE, 9, 6, b"\x00" * 6, 0, n_fat, dir_start, 0, 4096,
                     minifat_start, n_minifat, ENDOFCHAIN, 0)
    difat = list(range(n_fat)) + [FREESECT] * (109 - n_fat)
    struct.pack_into("<109I", header, 76, *difat)

    out = bytearray(header)
    out += struct.pack(f"<{len(fat)}I", *fat)
    out += d
    mf = struct.pack(f"<{len(minifat)}I", *minifat) if minifat else b""
    out += mf + b"\xff" * (n_minifat * 512 - len(mf))
    out += mini + b"\x00" * (n_mini * 512 - len(mini))
    for i in big:
        data = entries[i][3]
        out += data + b"\x00" * (sectors(len(data)) * 512 - len(data))
    return bytes(out)


# ================================================================ .msg (MS-OXMSG)

VARIABLE = {STR, BIN, OBJ, CLSID}


def _msg_props(store: Storage, props: dict, header: bytes):
    entries = bytearray(header)
    for pid, (ptype, value) in sorted(props.items()):
        tag = (pid << 16) | ptype
        if ptype in VARIABLE:
            data = encode(ptype, value)
            store.stream(f"__substg1.0_{pid:04X}{ptype:04X}", data)
            size = len(data) + (2 if ptype == STR else 0)
            entries += struct.pack("<IIII", tag, 6, size, 0)
        else:
            entries += struct.pack("<II", tag, 6) + encode(ptype, value).ljust(8, b"\x00")
    store.stream("__properties_version1.0", bytes(entries))


def _msg_message(store: Storage, m: Msg, top: bool):
    for i, r in enumerate(m.recipients):
        rs = store.storage(f"__recip_version1.0_#{i:08X}")
        _msg_props(rs, r, b"\x00" * 8)
    for i, a in enumerate(m.attachments):
        ast = store.storage(f"__attach_version1.0_#{i:08X}")
        props = dict(a.props)
        if a.message is not None:
            props[0x3705] = (LONG, 5)
            inner = ast.storage("__substg1.0_3701000D")
            _msg_message(inner, a.message, top=False)
            entries_extra = struct.pack("<IIII", (0x3701 << 16) | OBJ, 6, 0xFFFFFFFF, 0)
        else:
            props.setdefault(0x3705, (LONG, 1))
            props[0x3701] = (BIN, a.data or b"")
            entries_extra = b""
        _msg_props(ast, props, b"\x00" * 8)
        if entries_extra:
            ast.stream("__properties_version1.0", ast.children["__properties_version1.0"] + entries_extra)
    n_r, n_a = len(m.recipients), len(m.attachments)
    header = struct.pack("<8sIIII", b"\x00" * 8, n_r, n_a, n_r, n_a) + (b"\x00" * 8 if top else b"")
    _msg_props(store, m.props, header)


def write_msg(m: Msg) -> bytes:
    root = Storage("Root Entry")
    nameid = root.storage("__nameid_version1.0")
    for s in ("00020102", "00030102", "00040102"):
        nameid.stream(f"__substg1.0_{s}", b"")
    _msg_message(root, m, top=True)
    return cfb(root)


# ================================================================ .pst (MS-PST, Unicode)

BLOCK_MAX = 8176                 # data bytes in one block
HEAP_ITEM_MAX = 3580             # larger values go in subnodes
AMAP_SPAN = 496 * 8 * 64         # bytes one allocation map page covers
FIRST_AMAP = 0x4400
NID_MESSAGE_STORE, NID_NAME_TO_ID_MAP, NID_ROOT_FOLDER = 0x21, 0x61, 0x122
NID_RECIPIENT_TABLE, NID_ATTACHMENT_TABLE = 0x692, 0x671
T_FOLDER, T_MESSAGE, T_ATTACHMENT, T_LTP = 0x02, 0x04, 0x05, 0x1F

HIERARCHY_COLS = [(0x3001, STR), (0x3602, LONG), (0x3603, LONG), (0x360A, BOOL), (0x3613, STR)]
CONTENTS_COLS = [(0x001A, STR), (0x0037, STR), (0x0042, STR), (0x0E04, STR), (0x0E06, TIME), (0x0E07, LONG),
                 (0x0E08, LONG), (0x3008, TIME)]
RECIPIENT_COLS = [(0x0C15, LONG), (0x0E0F, BOOL), (0x0FFE, LONG), (0x3001, STR), (0x3002, STR), (0x3003, STR),
                  (0x3900, LONG), (0x39FE, STR)]
ATTACHMENT_COLS = [(0x0E20, LONG), (0x3704, STR), (0x3705, LONG), (0x370B, LONG)]


def _sig(ib: int, bid: int) -> int:
    v = (ib ^ bid) & 0xFFFFFFFF
    return ((v >> 16) ^ v) & 0xFFFF


class _Heap:
    """A heap-on-node (HN) being built: allocations in blocks of ≤ 8176 bytes."""

    def __init__(self, client_sig: int):
        self.client_sig = client_sig
        self.blocks: list[list[bytes]] = [[]]
        self.root = 0

    def _used(self, k):
        hdr = 12 if k == 0 else 2
        n = len(self.blocks[k])
        return hdr + sum(map(len, self.blocks[k])) + 1 + 4 + 2 * (n + 2)

    def alloc(self, data: bytes) -> int:
        assert 0 < len(data) <= HEAP_ITEM_MAX
        if self._used(len(self.blocks) - 1) + len(data) + 2 > BLOCK_MAX:
            assert len(self.blocks) < 8, "heap too large for this writer"
            self.blocks.append([])
        k = len(self.blocks) - 1
        self.blocks[k].append(data)
        return (k << 16) | (len(self.blocks[k]) << 5)

    def serialize(self) -> list[bytes]:
        out = []
        for k, allocs in enumerate(self.blocks):
            hdr = 12 if k == 0 else 2
            offsets = [hdr]
            for a in allocs:
                offsets.append(offsets[-1] + len(a))
            ib_map = offsets[-1] + (offsets[-1] & 1)
            body = b"".join(allocs)
            if k == 0:
                head = struct.pack("<HBBII", ib_map, 0xEC, self.client_sig, self.root, 0)
            else:
                head = struct.pack("<H", ib_map)
            pagemap = struct.pack("<HH", len(allocs), 0) + struct.pack(f"<{len(offsets)}H", *offsets)
            out.append(head + body + b"\x00" * (ib_map - offsets[-1]) + pagemap)
        return out


class PstWriter:
    def __init__(self, display_name: str):
        self.display_name = display_name
        self.blocks: list[tuple[int, bytes]] = []    # (bid, data)
        self.nodes: dict[int, tuple[int, int, int]] = {}   # nid → (bidData, bidSub, nidParent)
        self.next_bid = 1
        self.next_index = {T_FOLDER: 0x400, T_MESSAGE: 0x10000, T_ATTACHMENT: 0x400, T_LTP: 0x400}
        self.folders: dict[int, dict] = {}
        self.store_uid = bytes(range(0x50, 0x60))

    # -- blocks and nodes

    def _bid(self, internal=False) -> int:
        bid = (self.next_bid << 2) | (2 if internal else 0)
        self.next_bid += 1
        return bid

    def _block(self, data: bytes, internal=False) -> int:
        assert len(data) <= BLOCK_MAX
        bid = self._bid(internal)
        self.blocks.append((bid, data))
        return bid

    def _data(self, chunks: list[bytes]) -> int:
        """A data tree: one block, or an XBLOCK over several."""
        if len(chunks) == 1:
            return self._block(chunks[0])
        bids = [self._block(c) for c in chunks]
        assert len(bids) <= 1021
        total = sum(map(len, chunks))
        return self._block(struct.pack("<BBHI", 1, 1, len(bids), total) + struct.pack(f"<{len(bids)}Q", *bids),
                           internal=True)

    def _bytes(self, data: bytes) -> int:
        return self._data([data[i:i + BLOCK_MAX] for i in range(0, max(len(data), 1), BLOCK_MAX)])

    def _subnodes(self, subs: list[tuple[int, int, int]]) -> int:
        if not subs:
            return 0
        subs = sorted(subs)
        assert len(subs) <= 340
        return self._block(struct.pack("<BBHI", 2, 0, len(subs), 0) +
                           b"".join(struct.pack("<QQQ", n, d, s) for n, d, s in subs), internal=True)

    def _nid(self, typ: int) -> int:
        idx = self.next_index[typ]
        self.next_index[typ] += 1
        return (idx << 5) | typ

    # -- LTP: property contexts and tables

    def _value(self, heap: _Heap, subs: list, ptype: int, value) -> int:
        """A property's HNID (or inline value) for a PC or table cell."""
        if ptype in (LONG, I2, BOOL):
            return struct.unpack("<I", encode(ptype, value).ljust(4, b"\x00"))[0]
        data = encode(ptype, value)
        if len(data) <= HEAP_ITEM_MAX:
            return heap.alloc(data)
        nid = self._nid(T_LTP)
        subs.append((nid, self._bytes(data), 0))
        return nid

    def _pc(self, props: dict, subs: list) -> list[bytes]:
        heap = _Heap(0xBC)
        recs = []
        for pid, (ptype, value) in sorted(props.items()):
            if value is None:
                continue
            if ptype in (STR, BIN) and len(value) == 0:
                hnid = 0                      # an empty value
            elif ptype == OBJ:
                hnid = heap.alloc(struct.pack("<II", value[0], value[1]))
            else:
                hnid = self._value(heap, subs, ptype, value)
            recs.append(struct.pack("<HHI", pid, ptype, hnid))
        heap.root = self._bth(heap, 2, 6, recs)
        return heap.serialize()

    def _bth(self, heap: _Heap, cb_key: int, cb_ent: int, recs: list[bytes]) -> int:
        data = b"".join(recs)
        assert len(data) <= HEAP_ITEM_MAX, "BTH needs index levels: too many records for this writer"
        root = heap.alloc(data) if recs else 0
        return heap.alloc(struct.pack("<BBBBI", 0xB5, cb_key, cb_ent, 0, root))

    def _tc(self, cols: list[tuple[int, int]], rows: list[tuple[int, dict]], subs: list) -> list[bytes]:
        heap = _Heap(0x7C)
        size = lambda t: 8 if FIXED_SIZE.get(t, 4) == 8 else FIXED_SIZE.get(t, 4)
        allcols = [(0x67F2, LONG), (0x67F3, LONG)] + sorted(cols)
        layout = {}                                      # (pid, type) → (ib, cb, ibit)
        layout[allcols[0]] = (0, 4, 0)
        layout[allcols[1]] = (4, 4, 1)
        ib, ibit = 8, 2
        for want in (8, 4, 2, 1):
            for c in allcols[2:]:
                if size(c[1]) == want:
                    layout[c] = (ib, want, None)
                    ib += want
            if want == 4:
                end4 = ib
            elif want == 2:
                end2 = ib
            elif want == 1:
                end1 = ib
        for c in allcols[2:]:
            o, w, _ = layout[c]
            layout[c] = (o, w, ibit)
            ibit += 1
        row_size = end1 + (len(allcols) + 7) // 8
        matrix = bytearray()
        for rowid, values in rows:
            row = bytearray(row_size)
            values = {**values, 0x67F2: (LONG, rowid), 0x67F3: (LONG, 0)}
            for c in allcols:
                o, w, bit = layout[c]
                if c[0] not in values or values[c[0]][1] is None:
                    continue
                ptype, value = values[c[0]]
                if ptype in (STR, BIN) and len(value) == 0:
                    continue
                if w == 8:
                    row[o:o + 8] = encode(ptype, value).ljust(8, b"\x00")
                elif ptype in (LONG, I2, BOOL):
                    row[o:o + w] = encode(ptype, value).ljust(w, b"\x00")[:w]
                else:
                    struct.pack_into("<I", row, o, self._value(heap, subs, ptype, value))
                row[end1 + bit // 8] |= 0x80 >> (bit % 8)
            matrix += row
        index = self._bth(heap, 4, 4, [struct.pack("<II", rid, i) for i, (rid, _) in
                                        sorted(enumerate(rows), key=lambda x: x[1][0])])
        if not rows:
            hnid_rows = 0
        elif len(matrix) <= HEAP_ITEM_MAX:
            hnid_rows = heap.alloc(bytes(matrix))
        else:
            per = BLOCK_MAX // row_size
            chunks = [bytes(matrix[i:i + per * row_size]) for i in range(0, len(matrix), per * row_size)]
            nid = self._nid(T_LTP)
            subs.append((nid, self._data(chunks), 0))
            hnid_rows = nid
        descs = b"".join(struct.pack("<IHBB", (c[0] << 16) | c[1], *layout[c]) for c in
                         sorted(allcols, key=lambda c: (c[0] << 16) | c[1]))
        info = struct.pack("<BB4HIII", 0x7C, len(allcols), end4, end2, end1, row_size, index, hnid_rows, 0) + descs
        heap.root = heap.alloc(info)
        return heap.serialize()

    def _node(self, nid: int, heap_blocks: list[bytes], subs: list, parent: int):
        self.nodes[nid] = (self._data(heap_blocks), self._subnodes(subs), parent)

    # -- folders and messages

    def folder(self, name: str, parent: int | None = None, container_class="IPF.Note") -> int:
        nid = NID_ROOT_FOLDER if parent is None else self._nid(T_FOLDER)
        self.folders[nid] = {"name": name, "parent": parent if parent is not None else nid,
                             "class": container_class, "subfolders": [], "messages": []}
        if parent is not None:
            self.folders[parent]["subfolders"].append(nid)
        return nid

    def message(self, folder: int, m: Msg):
        nid = self._nid(T_MESSAGE)
        subs: list = []
        self._message_into(nid, m, subs)
        self.folders[folder]["messages"].append((nid, m))
        self.nodes[nid] = (self._last_pc, self._subnodes(subs), folder)

    def _message_into(self, nid: int, m: Msg, subs: list):
        """Write m's recipient and attachment tables and attachments into
        subs (its subnodes), and its property context; the PC's data BID is
        left in self._last_pc."""
        rec_rows = [(i, r) for i, r in enumerate(m.recipients)]
        subs.append((NID_RECIPIENT_TABLE, self._data(self._tc(RECIPIENT_COLS, rec_rows, subs)), 0))
        att_rows = []
        for a in m.attachments:
            anid = self._nid(T_ATTACHMENT)
            asubs: list = []
            props = dict(a.props)
            props.setdefault(0x370B, (LONG, -1 & 0xFFFFFFFF))
            if a.message is not None:
                props[0x3705] = (LONG, 5)
                mnid = self._nid(T_MESSAGE)
                msubs: list = []
                self._message_into(mnid, a.message, msubs)
                asubs.append((mnid, self._last_pc, self._subnodes(msubs)))
                props[0x3701] = (OBJ, (mnid, self._last_pc_size))
            else:
                props.setdefault(0x3705, (LONG, 1))
                props[0x3701] = (BIN, a.data or b"")
                props.setdefault(0x0E20, (LONG, len(a.data or b"")))
            pc = self._pc(props, asubs)
            subs.append((anid, self._data(pc), self._subnodes(asubs)))
            att_rows.append((anid, props))
        subs.append((NID_ATTACHMENT_TABLE, self._data(self._tc(ATTACHMENT_COLS, att_rows, subs)), 0))
        props = dict(m.props)
        flags = props.get(0x0E07, (LONG, 1))[1]
        if m.attachments:
            flags |= 0x10
            props[0x0E1B] = (BOOL, True)
        props[0x0E07] = (LONG, flags)
        pc = self._pc(props, subs)
        self._last_pc, self._last_pc_size = self._data(pc), sum(map(len, pc))

    # -- the file

    def _entryid(self, nid: int) -> bytes:
        return b"\x00" * 4 + self.store_uid + struct.pack("<I", nid)

    def _special(self, ipm: int, wastebasket: int | None):
        subs: list = []
        props = {0x0FF9: (BIN, self.store_uid), 0x3001: (STR, self.display_name),
                 0x35E0: (BIN, self._entryid(ipm))}
        if wastebasket:
            props[0x35E3] = (BIN, self._entryid(wastebasket))
        self._node(NID_MESSAGE_STORE, self._pc(props, subs), subs, 0)
        subs = []
        # One numeric named property, in PSETID_Common (the first GUID in the
        # GUID stream, index 3): readers expect the streams to hold something.
        psetid_common = bytes.fromhex("0820060000000000c000000000000046")
        self._node(NID_NAME_TO_ID_MAP, self._pc({0x0001: (LONG, 251), 0x0002: (BIN, psetid_common),
                                                 0x0003: (BIN, struct.pack("<IHH", 0x8501, 3 << 1, 0)),
                                                 0x0004: (BIN, b"")}, subs), subs, 0)
        for nid, cols in ((0x60D, HIERARCHY_COLS), (0x60E, CONTENTS_COLS), (0x60F, CONTENTS_COLS),
                          (0x671, ATTACHMENT_COLS), (0x692, RECIPIENT_COLS)):
            subs = []
            self._node(nid, self._tc(cols, [], subs), subs, 0)

    def _folders(self):
        for nid, f in self.folders.items():
            subs: list = []
            props = {0x3001: (STR, f["name"]), 0x3602: (LONG, len(f["messages"])), 0x3603: (LONG, 0),
                     0x360A: (BOOL, bool(f["subfolders"]))}
            if nid != NID_ROOT_FOLDER and f["class"]:
                props[0x3613] = (STR, f["class"])
            self._node(nid, self._pc(props, subs), subs, f["parent"])
            base = nid & ~0x1F
            rows = []
            for s in f["subfolders"]:
                sf = self.folders[s]
                rows.append((s, {0x3001: (STR, sf["name"]), 0x3602: (LONG, len(sf["messages"])), 0x3603: (LONG, 0),
                                 0x360A: (BOOL, bool(sf["subfolders"])), 0x3613: (STR, sf["class"] or "")}))
            subs = []
            self._node(base | 0x0D, self._tc(HIERARCHY_COLS, rows, subs), subs, 0)
            rows = []
            for mnid, m in f["messages"]:
                p = m.props
                rows.append((mnid, {k: p[k] for k, _ in CONTENTS_COLS if k in p}))
            subs = []
            self._node(base | 0x0E, self._tc(CONTENTS_COLS, rows, subs), subs, 0)
            subs = []
            self._node(base | 0x0F, self._tc(CONTENTS_COLS, [], subs), subs, 0)

    def write(self, ipm: int, wastebasket: int | None = None) -> bytes:
        self._special(ipm, wastebasket)
        self._folders()

        # Lay out blocks, then B-tree pages, skipping the allocation map pages.
        out = bytearray(FIRST_AMAP)
        amaps = []
        allocated = []                       # (ib, size)

        def reserve(size: int, align: int) -> int:
            pos = len(out)
            while True:
                pos = (pos + align - 1) // align * align
                k = (pos - FIRST_AMAP) // AMAP_SPAN
                amap = FIRST_AMAP + k * AMAP_SPAN
                fixed = 1024 if k % 8 == 0 else 512        # AMap, and a PMap with every eighth
                if pos < amap + fixed:
                    pos = amap + fixed
                    continue
                if pos + size > amap + AMAP_SPAN:
                    pos = amap + AMAP_SPAN
                    continue
                break
            if len(out) < pos + size:
                out.extend(b"\x00" * (pos + size - len(out)))
            allocated.append((pos, size))
            return pos

        bbt = []
        for bid, data in self.blocks:
            size = (len(data) + 16 + 63) // 64 * 64
            ib = reserve(size, 64)
            out[ib:ib + len(data)] = data
            struct.pack_into("<HHIQ", out, ib + size - 16, len(data), _sig(ib, bid), crc(data), bid)
            bbt.append((bid, ib, len(data)))

        next_page = [self.next_bid]

        def page(ptype: int, entries: list[bytes], cb_ent: int, level: int) -> tuple[int, int]:
            ib = reserve(512, 512)
            bid = next_page[0] << 2
            next_page[0] += 1
            body = b"".join(entries).ljust(488, b"\x00")
            body += struct.pack("<BBBB4x", len(entries), 488 // cb_ent, cb_ent, level)
            out[ib:ib + 496] = body
            struct.pack_into("<BBHIQ", out, ib + 496, ptype, ptype, _sig(ib, bid), crc(body), bid)
            return bid, ib

        def btree(ptype: int, leaves: list[tuple[int, bytes]], cb_ent: int) -> tuple[int, int]:
            per = 488 // cb_ent
            level, items = 0, leaves
            while True:
                pages = []
                for i in range(0, max(len(items), 1), per if level == 0 else 20):
                    group = items[i:i + (per if level == 0 else 20)]
                    pages.append((group[0][0] if group else 0,
                                  page(ptype, [e for _, e in group], cb_ent if level == 0 else 24, level)))
                if len(pages) == 1:
                    return pages[0][1]
                items = [(key, struct.pack("<QQQ", key, bid, ib)) for key, (bid, ib) in pages]
                level += 1

        bbt_root = btree(0x80, [(bid, struct.pack("<QQHHI", bid, ib, cb, 2, 0)) for bid, ib, cb in sorted(bbt)], 24)
        nbt_root = btree(0x81, [(nid, struct.pack("<QQQII", nid, d, s, p, 0))
                                for nid, (d, s, p) in sorted(self.nodes.items())], 32)
        eof = len(out)

        # Allocation maps: one bit per 64 bytes allocated.
        n_amaps = (eof - FIRST_AMAP + AMAP_SPAN - 1) // AMAP_SPAN
        free_total = 0
        for k in range(n_amaps):
            amap = FIRST_AMAP + k * AMAP_SPAN
            bits = bytearray(496)
            used = [(amap, 1024 if k % 8 == 0 else 512)] + [(ib, sz) for ib, sz in allocated
                                                            if amap <= ib < amap + AMAP_SPAN]
            for ib, sz in used:
                for u in range((ib - amap) // 64, (ib - amap + sz) // 64):
                    bits[u // 8] |= 0x80 >> (u % 8)
            covered = min(AMAP_SPAN, eof - amap)
            free_total += covered - sum(bin(b).count("1") for b in bits) * 64
            out[amap:amap + 496] = bits
            struct.pack_into("<BBHIQ", out, amap + 496, 0x84, 0x84, 0, crc(bytes(bits)), amap)
            if k % 8 == 0:
                pm = amap + 512
                out[pm:pm + 496] = b"\xff" * 496
                struct.pack_into("<BBHIQ", out, pm + 496, 0x83, 0x83, 0, crc(b"\xff" * 496), pm)
        amaps = [FIRST_AMAP + k * AMAP_SPAN for k in range(n_amaps)]

        header = bytearray(564)
        rgnid = [0] * 32
        for typ, idx in self.next_index.items():
            rgnid[typ] = (idx << 5) | typ
        root = struct.pack("<IQQQQQQQQBBH", 0, eof, amaps[-1], free_total, 0,
                           nbt_root[0], nbt_root[1], bbt_root[0], bbt_root[1], 2, 0, 0)
        struct.pack_into("<4sIHHHBBII", header, 0, b"!BDN", 0, 0x4D53, 23, 19, 1, 1, 0, 0)
        struct.pack_into("<QQI", header, 24, 0, next_page[0] << 2, 1)
        struct.pack_into("<32I", header, 44, *rgnid)
        struct.pack_into("<Q", header, 172, 0)
        header[180:252] = root
        header[256:384] = b"\xff" * 128
        header[384:512] = b"\xff" * 128
        header[512] = 0x80
        header[513] = 0                        # no encryption
        struct.pack_into("<Q", header, 516, self.next_bid << 2)
        struct.pack_into("<I", header, 4, crc(bytes(header[8:8 + 471])))
        struct.pack_into("<I", header, 524, crc(bytes(header[8:8 + 516])))
        out[:564] = header
        return bytes(out)


# ================================================================ helpers for building messages

def addr_props(name: str, smtp: str, exchange_dn: str | None = None, kind: int = 1) -> dict:
    """A recipient's properties (kind: 1 To, 2 Cc, 3 Bcc): an Exchange
    address with an SMTP one, or SMTP alone."""
    p = {0x0C15: (LONG, kind), 0x3001: (STR, name), 0x0FFE: (LONG, 6), 0x3900: (LONG, 0), 0x39FE: (STR, smtp)}
    if exchange_dn:
        p[0x3002], p[0x3003] = (STR, "EX"), (STR, exchange_dn)
    else:
        p[0x3002], p[0x3003] = (STR, "SMTP"), (STR, smtp)
    return p
