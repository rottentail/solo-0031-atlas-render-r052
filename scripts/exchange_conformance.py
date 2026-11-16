#!/usr/bin/env python3
"""Atlas scene exchange format v1 — reference conformance checker.

Pure standard library; does not import Atlas Go code and does not contact a
server. It validates the *format layer* of each vector in an export
directory and recomputes both digests from the normative canonical encoding
described in docs/exchange.md.

Usage:
    python3 scripts/exchange_conformance.py docs/exchange-vectors

Exit status is 0 when every vector matches its manifest entry's format-layer
expectation, 1 otherwise.
"""
import hashlib
import json
import math
import sys
from decimal import Decimal
from pathlib import Path

MAX_PACKAGE = 12 << 20
MAX_ASSET = 2 << 20
MAX_ASSET_SIDE = 1024


# ---------------------------------------------------------------------------
# Canonical encoding (docs/exchange.md section 4)
#
# Struct members keep declaration order; map entries sort by UTF-8 bytes.
# These two container types make the distinction explicit so that a mistake
# cannot silently pass.
# ---------------------------------------------------------------------------
class Struct(list):
    """Ordered (member, value) pairs, emitted in declaration order."""


class Map(dict):
    """Unordered entries, emitted sorted by key."""


def _go_string(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == '\\':
            out.append('\\\\')
        elif ch == '\n':
            out.append('\\n')
        elif ch == '\r':
            out.append('\\r')
        elif ch == '\t':
            out.append('\\t')
        elif ch == '\b':
            out.append('\\b')
        elif ch == '\f':
            out.append('\\f')
        elif o < 0x20:
            out.append('\\u%04x' % o)
        elif ch in '&<>':
            out.append('\\u%04x' % o)
        elif o in (0x2028, 0x2029):
            out.append('\\u%04x' % o)
        elif 0xD800 <= o <= 0xDFFF:
            out.append('�')
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)


def _go_float(f):
    """encoding/json float64 formatting: shortest round-trip digits, fixed
    form for 1e-6 <= |x| < 1e21, exponent form otherwise."""
    if f != f or f in (float('inf'), float('-inf')):
        raise ValueError('non-finite number is not representable')
    neg = math.copysign(1, f) < 0
    if f == 0:
        return '-0' if neg else '0'
    t = Decimal(repr(abs(f))).as_tuple()
    digits = ''.join(str(d) for d in t.digits).lstrip('0')
    exp = t.exponent
    while len(digits) > 1 and digits.endswith('0'):
        digits = digits[:-1]
        exp += 1
    if not digits:
        digits, exp = '0', 0
    sign = '-' if neg else ''
    a = abs(f)
    if a < 1e-6 or a >= 1e21:
        e = exp + len(digits) - 1
        body = digits[0] + ('.' + digits[1:] if len(digits) > 1 else '')
        return '%s%se%s%d' % (sign, body, '+' if e >= 0 else '-', abs(e))
    point = len(digits) + exp
    if point <= 0:
        return sign + '0.' + '0' * (-point) + digits
    if point >= len(digits):
        return sign + digits + '0' * (point - len(digits))
    return sign + digits[:point] + '.' + digits[point:]


def canonical(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return _go_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _go_float(value)
    if isinstance(value, Struct):
        return '{' + ','.join(_go_string(k) + ':' + canonical(v) for k, v in value) + '}'
    if isinstance(value, Map):
        return '{' + ','.join(_go_string(k) + ':' + canonical(value[k])
                              for k in sorted(value)) + '}'
    if isinstance(value, list):
        return '[' + ','.join(canonical(v) for v in value) + ']'
    raise TypeError(f'cannot canonicalise {type(value).__name__}')


# ---------------------------------------------------------------------------
# Typed reconstruction: apply declaration order and schema defaults.
# Unknown members are reported separately by the caller.
# ---------------------------------------------------------------------------
LAYER_MEMBERS = ('id', 'kind', 'width', 'height', 'transform', 'opacity', 'fill',
                 'points', 'assetId', 'clip', 'children')
DOCUMENT_MEMBERS = ('width', 'height', 'background', 'layers')
ASSET_MEMBERS = ('id', 'digest', 'state', 'width', 'height', 'png')
SNAPSHOT_MEMBERS = ('id', 'sceneId', 'revision', 'state', 'document', 'assets',
                    'digest', 'createdAt')
PACKAGE_MEMBERS = ('format', 'version', 'snapshot', 'digest')


def _obj(v, what):
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ValueError(f'{what} must be an object')
    return v


def _point(p):
    p = _obj(p, 'point')
    return Struct([('x', p.get('x', 0)), ('y', p.get('y', 0))])


def _rect(r):
    r = _obj(r, 'clip')
    return Struct([('x', r.get('x', 0)), ('y', r.get('y', 0)),
                   ('width', r.get('width', 0)), ('height', r.get('height', 0))])


def _paint(p):
    p = _obj(p, 'fill')
    items = [('kind', p.get('kind', '')), ('color', p.get('color', ''))]
    if p.get('endColor'):
        items.append(('endColor', p['endColor']))
    items += [('from', _point(p.get('from'))), ('to', _point(p.get('to')))]
    return Struct(items)


def _transform(t):
    t = _obj(t, 'transform')
    return Struct([('x', t.get('x', 0)), ('y', t.get('y', 0)),
                   ('scaleX', t.get('scaleX', 0)), ('scaleY', t.get('scaleY', 0)),
                   ('degrees', t.get('degrees', 0))])


def _layer(l):
    l = _obj(l, 'layer')
    items = [('id', l.get('id', '')), ('kind', l.get('kind', '')),
             ('width', l.get('width', 0)), ('height', l.get('height', 0)),
             ('transform', _transform(l.get('transform'))),
             ('opacity', l.get('opacity', 0)), ('fill', _paint(l.get('fill')))]
    if l.get('points'):
        items.append(('points', [_point(p) for p in l['points']]))
    if l.get('assetId'):
        items.append(('assetId', l['assetId']))
    if l.get('clip') is not None:
        items.append(('clip', _rect(l['clip'])))
    if l.get('children'):
        items.append(('children', [_layer(c) for c in l['children']]))
    return Struct(items)


def _document(d):
    d = _obj(d, 'document')
    layers = d.get('layers')
    return Struct([('width', d.get('width', 0)), ('height', d.get('height', 0)),
                   ('background', d.get('background', '')),
                   ('layers', None if layers is None else [_layer(x) for x in layers])])


def _asset(a):
    a = _obj(a, 'asset')
    return Struct([('id', a.get('id', '')), ('digest', a.get('digest', '')),
                   ('state', a.get('state', '')), ('width', a.get('width', 0)),
                   ('height', a.get('height', 0)), ('png', a.get('png', ''))])


def _assets(m):
    if m is None:
        return None
    if not isinstance(m, dict):
        raise ValueError('assets must be an object or null')
    return Map({k: _asset(v) for k, v in m.items()})


def _canonical_time(raw):
    """RFC 3339 as Go's time.Time re-renders it: same instant, same offset
    (zero offset becomes Z), shortest fractional digits, no fraction when 0."""
    import datetime
    import re

    if not raw:
        return '0001-01-01T00:00:00Z'
    m = re.fullmatch(
        r'(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})', raw)
    if not m:
        raise ValueError(f'createdAt is not RFC 3339: {raw!r}')
    date, clock, frac, offset = m.groups()
    frac = (frac or '').rstrip('0')
    if offset == 'Z' or offset in ('+00:00', '-00:00'):
        suffix = 'Z'
    else:
        suffix = offset
    try:
        datetime.date.fromisoformat(date)
    except ValueError as exc:
        raise ValueError(f'createdAt has an invalid date: {raw!r}') from exc
    return f'{date}T{clock}' + (f'.{frac}' if frac else '') + suffix


def _snapshot(s):
    s = _obj(s, 'snapshot')
    return Struct([('id', s.get('id', '')), ('sceneId', s.get('sceneId', '')),
                   ('revision', s.get('revision', 0)), ('state', s.get('state', '')),
                   ('document', _document(s.get('document'))),
                   ('assets', _assets(s.get('assets'))),
                   ('digest', s.get('digest', '')),
                   ('createdAt', _canonical_time(s.get('createdAt')))])


def inner_preimage(snapshot):
    s = _obj(snapshot, 'snapshot')
    return canonical(Struct([('Document', _document(s.get('document'))),
                             ('Assets', _assets(s.get('assets')))]))


def outer_preimage(snapshot):
    return canonical(_snapshot(snapshot))


def sha256_text(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# PNG structural check without Pillow. Decompresses IDAT to confirm the data
# stream is well formed and reports the true dimensions from IHDR.
# ---------------------------------------------------------------------------
def png_info(data):
    import struct
    import zlib

    if len(data) < 8 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('not a PNG')
    i, idat, width, height, bit_depth, color_type = 8, bytearray(), None, None, None, None
    seen_iend = False
    while i + 8 <= len(data):
        n = struct.unpack('>I', data[i:i + 4])[0]
        tag = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + n]
        if len(body) != n:
            raise ValueError('truncated chunk')
        i += 12 + n
        if tag == b'IHDR':
            if n != 13:
                raise ValueError('bad IHDR length')
            width, height, bit_depth, color_type, comp, filt, inter = \
                struct.unpack('>IIBBBBB', body)
            if comp or filt or inter:
                raise ValueError('unsupported PNG encoding')
        elif tag == b'IDAT':
            idat += body
        elif tag == b'IEND':
            seen_iend = True
            break
    if width is None or not seen_iend:
        raise ValueError('missing IHDR or IEND')
    try:
        zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise ValueError(f'bad IDAT: {exc}') from exc
    return {'width': width, 'height': height,
            'bitDepth': bit_depth, 'colorType': color_type}


def _unknown_member(obj, allowed, where):
    if not isinstance(obj, dict):
        return None
    for key in obj:
        if key not in allowed:
            return f'invalid JSON: json: unknown field {json.dumps(key)} at {where}'
    return None


def _unknown_members(pkg):
    """The HTTP entry point rejects unknown members at every level. This
    checker models that profile, because that is the behaviour a third party
    exchanging packages over the documented API will observe."""
    if not isinstance(pkg, dict):
        return None
    bad = _unknown_member(pkg, PACKAGE_MEMBERS, 'package')
    if bad:
        return bad
    snapshot = pkg.get('snapshot')
    bad = _unknown_member(snapshot, SNAPSHOT_MEMBERS, 'snapshot')
    if bad:
        return bad
    if not isinstance(snapshot, dict):
        return None

    def walk(obj, allowed, where):
        bad = _unknown_member(obj, allowed, where)
        if bad:
            return bad
        if not isinstance(obj, dict):
            return None
        return None

    document = snapshot.get('document')
    bad = walk(document, DOCUMENT_MEMBERS, 'document')
    if bad:
        return bad

    def walk_layers(layers, depth):
        if not isinstance(layers, list):
            return None
        for layer in layers:
            bad = walk(layer, LAYER_MEMBERS, 'layer')
            if bad:
                return bad
            if not isinstance(layer, dict):
                continue
            for nested, members, name in (
                    (layer.get('transform'), ('x', 'y', 'scaleX', 'scaleY', 'degrees'), 'transform'),
                    (layer.get('fill'), ('kind', 'color', 'endColor', 'from', 'to'), 'fill'),
                    (layer.get('clip'), ('x', 'y', 'width', 'height'), 'clip'),
                    (layer.get('from'), ('x', 'y'), 'from'),
                    (layer.get('to'), ('x', 'y'), 'to')):
                bad = walk(nested, members, name)
                if bad:
                    return bad
            fill = layer.get('fill')
            if isinstance(fill, dict):
                for key in ('from', 'to'):
                    bad = walk(fill.get(key), ('x', 'y'), f'fill.{key}')
                    if bad:
                        return bad
            if isinstance(layer.get('points'), list):
                for p in layer['points']:
                    bad = walk(p, ('x', 'y'), 'point')
                    if bad:
                        return bad
            bad = walk_layers(layer.get('children'), depth + 1)
            if bad:
                return bad
        return None

    bad = walk_layers((document or {}).get('layers') if isinstance(document, dict) else None, 0)
    if bad:
        return bad

    assets = snapshot.get('assets')
    if isinstance(assets, dict):
        for name, a in assets.items():
            bad = walk(a, ASSET_MEMBERS, f'asset {name}')
            if bad:
                return bad
    return None


def _check_document(document):
    """Mirror scene.Validate for the fields the vectors and the documented
    constraints cover. Returns an error string or None."""
    if not isinstance(document, dict):
        return 'document must be an object'
    w, h = document.get('width'), document.get('height')
    if not (isinstance(w, int) and not isinstance(w, bool) and 1 <= w <= 1536):
        return 'canvas dimensions must be 1..1536'
    if not (isinstance(h, int) and not isinstance(h, bool) and 1 <= h <= 1536):
        return 'canvas dimensions must be 1..1536'
    bg = document.get('background')
    if not _valid_colour(bg):
        return 'background: color must be #RRGGBB'

    seen = set()
    count = 0

    def finite(v):
        return (isinstance(v, (int, float)) and not isinstance(v, bool)
                and v == v and abs(v) != float('inf') and abs(v) <= 100000)

    def walk(layers, depth):
        nonlocal count
        if depth > 8:
            return 'scene nesting exceeds eight'
        if not isinstance(layers, list):
            return None
        for layer in layers:
            if not isinstance(layer, dict):
                return 'layer must be an object'
            count += 1
            if count > 200:
                return 'scene exceeds 200 layers'
            lid = layer.get('id')
            if not isinstance(lid, str) or not _valid_id(lid) or lid in seen:
                return f'layer id invalid or repeated: {lid}'
            seen.add(lid)
            kind = layer.get('kind')
            t = layer.get('transform') or {}
            if not isinstance(t, dict):
                return f'layer {lid}: transform must be an object'
            for name in ('width', 'height', 'opacity'):
                if not finite(layer.get(name, 0)):
                    return f'layer {lid}: nonfinite or unbounded geometry'
            for name in ('x', 'y', 'scaleX', 'scaleY', 'degrees'):
                if not finite(t.get(name, 0)):
                    return f'layer {lid}: nonfinite or unbounded geometry'
            opacity = layer.get('opacity', 0)
            if not (0 <= opacity <= 1):
                return f'layer {lid}: invalid opacity or scale'
            for name in ('scaleX', 'scaleY'):
                v = t.get(name, 0)
                if not (0.001 <= abs(v) <= 100):
                    return f'layer {lid}: invalid opacity or scale'
            clip = layer.get('clip')
            if clip is not None:
                if not isinstance(clip, dict):
                    return f'layer {lid}: invalid clip'
                for name in ('x', 'y', 'width', 'height'):
                    if not finite(clip.get(name, 0)):
                        return f'layer {lid}: invalid clip'
                if clip.get('width', 0) <= 0 or clip.get('height', 0) <= 0:
                    return f'layer {lid}: invalid clip'
            if kind == 'group':
                children = layer.get('children')
                if not children:
                    return f'group {lid} is empty'
                err = walk(children, depth + 1)
                if err:
                    return err
                continue
            if layer.get('children'):
                return 'only groups can contain layers'
            lw, lh = layer.get('width', 0), layer.get('height', 0)
            if lw <= 0 or lh <= 0 or lw > 4096 or lh > 4096:
                return f'layer {lid}: invalid dimensions'
            if kind not in ('rectangle', 'ellipse', 'polygon', 'image'):
                return f'layer {lid}: unknown kind'
            if kind == 'polygon':
                points = layer.get('points') or []
                if not (3 <= len(points) <= 128):
                    return 'polygon requires 3..128 points'
                for p in points:
                    if not isinstance(p, dict):
                        return 'polygon point must be an object'
                    if not finite(p.get('x', 0)) or not finite(p.get('y', 0)):
                        return 'polygon point outside local bounds'
                    if not (0 <= p.get('x', 0) <= lw and 0 <= p.get('y', 0) <= lh):
                        return 'polygon point outside local bounds'
            if kind == 'image':
                if not _valid_id(layer.get('assetId') or ''):
                    return 'image requires asset id'
                continue
            fill = layer.get('fill') or {}
            if not isinstance(fill, dict):
                return 'fill must be an object'
            if fill.get('kind') not in ('solid', 'linear'):
                return 'unknown paint kind'
            if not _valid_colour(fill.get('color')):
                return 'invalid fill color'
            if fill.get('kind') == 'linear':
                if not _valid_colour(fill.get('endColor')):
                    return 'invalid gradient color'
                frm = fill.get('from') or {}
                to = fill.get('to') or {}
                if not isinstance(frm, dict) or not isinstance(to, dict):
                    return 'invalid gradient endpoint'
                for p in (frm, to):
                    if not finite(p.get('x', 0)) or not finite(p.get('y', 0)):
                        return 'invalid gradient endpoint'
                if frm.get('x', 0) == to.get('x', 0) and frm.get('y', 0) == to.get('y', 0):
                    return 'gradient endpoints coincide'
        return None

    return walk(document.get('layers'), 0)


def _valid_id(value):
    import re
    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', value or ''))


def _valid_colour(value):
    import re
    return isinstance(value, str) and bool(re.fullmatch(r'#[0-9A-Fa-f]{6}', value))


# ---------------------------------------------------------------------------
# Format-layer validation, mirroring docs/exchange.md section 6.1 steps 4-12.
# Returns (ok, reason).
# ---------------------------------------------------------------------------
def validate(pkg, raw_size):
    if raw_size > MAX_PACKAGE:
        return False, 'package exceeds 12MiB'
    if not isinstance(pkg, dict):
        return False, 'package must be an object'
    unknown = _unknown_members(pkg)
    if unknown:
        return False, unknown
    if pkg.get('format') != 'atlas-scene' or pkg.get('version') != 1:
        return False, 'unsupported scene package'
    snapshot = pkg.get('snapshot')
    if not isinstance(snapshot, dict):
        return False, 'snapshot must be an object'
    declared_outer = pkg.get('digest')
    if not isinstance(declared_outer, str):
        return False, 'package checksum mismatch'
    try:
        outer = outer_preimage(snapshot)
    except (TypeError, ValueError) as exc:
        return False, str(exc)
    if sha256_text(outer) != declared_outer:
        return False, 'package checksum mismatch'

    document = snapshot.get('document')
    err = _check_document(document)
    if err:
        return False, err

    declared_inner = snapshot.get('digest')
    if not isinstance(declared_inner, str):
        return False, 'snapshot digest mismatch'
    if sha256_text(inner_preimage(snapshot)) != declared_inner:
        return False, 'snapshot digest mismatch'

    refs = referenced_assets(document)
    assets = snapshot.get('assets')
    if assets is None:
        assets = {}
    if not isinstance(assets, dict):
        return False, 'snapshot asset closure mismatch'
    # The reference implementation compares cardinality first, then presence
    # per referenced id, so an extra and a missing id report differently.
    if len(assets) != len(refs):
        return False, 'snapshot asset closure mismatch'

    import base64
    import binascii
    for aid in sorted(refs):
        if aid not in assets:
            return False, 'snapshot asset missing'
        a = assets[aid]
        if not isinstance(a, dict) or a.get('id') != aid:
            return False, 'snapshot asset missing'
        png = a.get('png')
        if not isinstance(png, str):
            return False, 'asset digest mismatch'
        try:
            blob = base64.b64decode(png, validate=True)
        except (binascii.Error, ValueError):
            return False, 'invalid base64 PNG'
        if hashlib.sha256(blob).hexdigest() != a.get('digest'):
            return False, 'asset digest mismatch'
        if not 1 <= len(blob) <= MAX_ASSET:
            return False, 'PNG size must be 1..2MiB'
        try:
            info = png_info(blob)
        except ValueError as exc:
            return False, f'invalid PNG: {exc}'
        if not (1 <= info['width'] <= MAX_ASSET_SIDE and
                1 <= info['height'] <= MAX_ASSET_SIDE):
            return False, 'asset dimensions must be 1..1024'
        if info['width'] != a.get('width') or info['height'] != a.get('height'):
            return False, 'asset dimensions mismatch'
    return True, ''


def referenced_assets(document):
    out = set()

    def walk(layers):
        for layer in layers or []:
            if isinstance(layer, dict):
                if layer.get('kind') == 'image' and layer.get('assetId'):
                    out.add(layer['assetId'])
                walk(layer.get('children'))

    walk(document.get('layers'))
    return out


# ---------------------------------------------------------------------------
# Manifest driven check
# ---------------------------------------------------------------------------
def classify_reason(reason):
    """Map an internal rejection reason to the stable error class used in the
    manifest. Implementations may word their diagnostics differently; the
    class is what a third party should rely on."""
    table = (
        ('unsupported scene package', 'unsupported-envelope'),
        ('unknown field', 'unknown-member'),
        ('package excludes', 'package-too-large'),
        ('package exceeds', 'package-too-large'),
        ('package checksum mismatch', 'outer-checksum-mismatch'),
        ('snapshot digest mismatch', 'inner-checksum-mismatch'),
        ('snapshot asset closure mismatch', 'closure-mismatch'),
        ('snapshot asset missing', 'asset-key-mismatch'),
        ('asset digest mismatch', 'asset-digest-mismatch'),
        ('asset dimensions mismatch', 'asset-dimensions-mismatch'),
        ('asset dimensions must be', 'asset-dimensions-limit'),
        ('PNG size must be', 'asset-size-limit'),
        ('invalid PNG', 'asset-not-decodable'),
        ('invalid base64', 'asset-not-decodable'),
        ('canvas dimensions', 'invalid-document'),
        ('nesting exceeds', 'invalid-document'),
        ('exceeds 200 layers', 'invalid-document'),
        ('layer', 'invalid-document'),
        ('group', 'invalid-document'),
        ('only groups', 'invalid-document'),
        ('polygon', 'invalid-document'),
        ('unknown paint kind', 'invalid-document'),
        ('gradient', 'invalid-document'),
        ('background', 'invalid-document'),
        ('fill', 'invalid-document'),
        ('document must be', 'invalid-document'),
        ('must be an object', 'malformed'),
    )
    for needle, cls in table:
        if reason.startswith(needle) or needle in reason:
            return cls
    return 'other'


def check(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    failures = 0
    for entry in manifest:
        path = directory / entry['file']
        raw = path.read_bytes()
        problems = []
        if hashlib.sha256(raw).hexdigest() != entry['fileSha256']:
            problems.append('vector file hash differs from manifest')
        try:
            pkg = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f'JSON does not parse: {exc}')
            pkg = None
        if pkg is not None:
            ok, reason = validate(pkg, len(raw))
            if entry['expect'] == 'accept':
                if not ok:
                    problems.append(f'expected accept, format layer rejected: {reason}')
                else:
                    snap = pkg['snapshot']
                    if entry.get('innerPreimage') is not None:
                        if inner_preimage(snap) != entry['innerPreimage']:
                            problems.append('inner preimage differs')
                    if entry.get('outerPreimage') is not None:
                        if outer_preimage(snap) != entry['outerPreimage']:
                            problems.append('outer preimage differs')
                    if entry.get('innerDigest') and \
                            sha256_text(inner_preimage(snap)) != entry['innerDigest']:
                        problems.append('inner digest differs')
                    if entry.get('outerDigest') and \
                            sha256_text(outer_preimage(snap)) != entry['outerDigest']:
                        problems.append('outer digest differs')
            else:
                if ok:
                    problems.append('expected reject, format layer accepted the package')
                else:
                    # Compare on the stable error class. Exact wording is a
                    # property of one implementation, not of the format.
                    want = entry.get('errorClass')
                    got = classify_reason(reason)
                    if want and got != want:
                        problems.append(f'error class differs: got {got!r} '
                                        f'({reason!r}), manifest {want!r}')
        status = 'ok' if not problems else 'FAIL'
        if problems:
            failures += 1
        print(f'{entry["name"]:<40} {status}')
        for p in problems:
            print(f'    - {p}')
    print(f'{len(manifest)} vectors, {failures} failing')
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        sys.exit(2)
    sys.exit(check(sys.argv[1]))
