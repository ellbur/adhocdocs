// Finding known entities' aliases in layer text, for the entity layer.
// The same rules as src/adhocdocs/entities.py (its docstring has them);
// tests/entity_match_cases.json holds both to them. Offsets are code points.
"use strict";

const ENTITY_WORD = /[\p{L}\p{N}_]/u;
const ENTITY_UPPER = /\p{Lu}/u;
const ENTITY_MARK = /\p{Mn}/gu;
const entityFoldCache = new Map();

function entityFold(c) {
  let f = entityFoldCache.get(c);
  if (f !== undefined) return f;
  f = c.normalize("NFKD").replace(ENTITY_MARK, "").toLowerCase();
  if ([...f].length !== 1) {
    const lo = c.toLowerCase();
    f = [...lo].length === 1 ? lo : c;
  }
  entityFoldCache.set(c, f);
  return f;
}

// The folded text as a string, and for each UTF-16 index of it the code point index.
function entityFoldText(cps) {
  const parts = new Array(cps.length);
  let astral = false;
  for (let i = 0; i < cps.length; i++) {
    const f = entityFold(cps[i]);
    parts[i] = f;
    if (f.length > 1) astral = true;
  }
  const folded = parts.join("");
  let cpAt = null;
  if (astral) {
    cpAt = new Int32Array(folded.length + 1);
    let u = 0;
    for (let i = 0; i < parts.length; i++) { for (let k = 0; k < parts[i].length; k++) cpAt[u + k] = i; u += parts[i].length; }
    cpAt[u] = parts.length;
  }
  return { folded, cpAt };
}

const entityEscape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");   // the u flag allows no other escapes

// cps: the text as an array of code points (layerText(...).cps). entities:
// [{entity, aliases}]. Returns [{start, end, entities: [ID]}], in order.
function findEntityMentions(cps, entities, folded) {
  const ft = folded || entityFoldText(cps);
  const toCp = (u) => (ft.cpAt ? ft.cpAt[u] : u);
  const cands = new Map();                  // "s,e" -> {start, end, entities}
  for (const ent of entities) {
    for (const alias of ent.aliases || []) {
      const words = alias.split(/\s+/).filter(Boolean);
      if (!words.length) continue;
      const re = new RegExp(words.map((w) => entityEscape(Array.from(w).map(entityFold).join(""))).join("\\s+"), "gu");
      const capital = ENTITY_UPPER.test(alias);
      let m;
      while ((m = re.exec(ft.folded))) {
        if (m[0].length === 0) { re.lastIndex++; continue; }
        const s = toCp(m.index), e = toCp(m.index + m[0].length);
        if ((s > 0 && ENTITY_WORD.test(cps[s - 1])) || (e < cps.length && ENTITY_WORD.test(cps[e]))) continue;
        if (capital && !cps.slice(s, e).some((c) => ENTITY_UPPER.test(c))) continue;
        const k = s + "," + e;
        let c = cands.get(k);
        if (!c) cands.set(k, (c = { start: s, end: e, entities: [] }));
        if (!c.entities.includes(ent.entity)) c.entities.push(ent.entity);
      }
    }
  }
  const out = [];
  let last = 0;
  for (const c of [...cands.values()].sort((a, b) => a.start - b.start || b.end - a.end)) {
    if (c.start < last) continue;
    out.push(c);
    last = c.end;
  }
  return out;
}

if (typeof module !== "undefined") module.exports = { findEntityMentions, entityFold, entityFoldText };
