() => {
  // Rhapsode page extractor. Runs inside the page (Chrome DevTools MCP
  // evaluate_script) and returns the main readable content as JSON:
  // { url, title, site, lang, sections: [{ heading, level, blocks: [...] }] }
  // Block kinds: p, li, quote, code, table. Read-only: never touches the DOM.

  const SKIP_SEL = [
    'nav', 'aside', 'footer', 'script', 'style', 'noscript', 'template', 'svg',
    'canvas', 'iframe', 'form', 'button', 'select', 'input', 'textarea',
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]',
    '[role="complementary"]', '[role="search"]', '[aria-hidden="true"]',
    '[hidden]', '.sr-only', '.visually-hidden',
  ].join(',');
  const NOISE = /\b(share|sharing|social|comments?|related|promo|advert|ads|cookie|newsletter|subscribe|breadcrumbs?|sidebar|popup|modal|paywall)\b/i;
  const STRIP_SEL = 'sup, [data-type="noteref"], .footnote-ref, script, style, button, svg, img, [aria-hidden="true"], .sr-only, .visually-hidden';
  const BLOCK_SEL = 'p, li, blockquote, pre, table, h1, h2, h3, h4, h5, h6, dt, dd, figcaption, ul, ol, dl';

  const clean = (s) => s.replace(/\s+/g, ' ').trim();
  const text = (el) => {
    const c = el.cloneNode(true);
    c.querySelectorAll(STRIP_SEL).forEach((n) => n.remove());
    return clean(c.textContent || '');
  };
  const pText = (el) => [...el.querySelectorAll('p')].reduce((n, p) => n + (p.textContent || '').length, 0);
  const hidden = (el) => {
    const cs = getComputedStyle(el);
    return cs.display === 'none' || cs.visibility === 'hidden';
  };
  const skip = (el) =>
    el.matches(SKIP_SEL) || NOISE.test(`${el.id} ${typeof el.className === 'string' ? el.className : ''}`) || hidden(el);

  // Root: the candidate holding (nearly) the most paragraph text, preferring
  // the tightest one so site chrome around the article is left out.
  const sel = 'article, main, [role="main"], #main, #content, .content, .post, .entry-content, .article-body, #book-content, #sbo-rt-content';
  const cands = new Set([...document.querySelectorAll(sel), document.body]);
  const tally = new Map();
  document.querySelectorAll('p').forEach((p) => {
    const n = (p.textContent || '').length;
    if (p.parentElement) tally.set(p.parentElement, (tally.get(p.parentElement) || 0) + n);
    const gp = p.parentElement && p.parentElement.parentElement;
    if (gp) tally.set(gp, (tally.get(gp) || 0) + n / 2);
  });
  [...tally.entries()].sort((a, b) => b[1] - a[1]).slice(0, 3).forEach(([el]) => cands.add(el));
  const scored = [...cands].map((el) => ({ el, p: pText(el), all: (el.textContent || '').length }));
  const best = Math.max(...scored.map((c) => c.p));
  const root = scored.filter((c) => c.p >= best * 0.9).sort((a, b) => a.all - b.all)[0].el;

  const h1 = root.querySelector('h1') || document.querySelector('h1');
  const title = clean((h1 && h1.textContent) || document.title || location.hostname);
  const sections = [{ heading: title, level: 1, blocks: [] }];
  const cur = () => sections[sections.length - 1];
  const push = (kind, t) => {
    if (t) cur().blocks.push({ kind, text: t });
  };

  const table = (el) => {
    const rows = [...el.rows].map((r) => [...r.cells].map((c) => text(c)));
    if (!rows.length) return;
    const first = el.rows[0];
    const hasHead = el.tHead || [...first.cells].every((c) => c.tagName === 'TH');
    const header = hasHead ? rows.shift() : [];
    const cap = el.caption ? text(el.caption) : '';
    cur().blocks.push({ kind: 'table', caption: cap, header, rows });
  };

  const walk = (el) => {
    for (const child of el.children) {
      if (skip(child)) continue;
      const tag = child.tagName;
      if (/^H[1-6]$/.test(tag)) {
        const t = text(child);
        if (!t) continue;
        if (tag === 'H1' && t === title && cur().blocks.length === 0 && sections.length === 1) continue;
        sections.push({ heading: t, level: Number(tag[1]), blocks: [] });
      } else if (tag === 'P' || tag === 'DT' || tag === 'DD' || tag === 'FIGCAPTION' || tag === 'SUMMARY') {
        push('p', text(child));
      } else if (tag === 'BLOCKQUOTE') {
        push('quote', text(child));
      } else if (tag === 'LI') {
        const own = child.cloneNode(true);
        own.querySelectorAll('ul, ol').forEach((n) => n.remove());
        own.querySelectorAll(STRIP_SEL).forEach((n) => n.remove());
        push('li', clean(own.textContent || ''));
        child.querySelectorAll(':scope > ul, :scope > ol').forEach(walk);
      } else if (tag === 'PRE') {
        push('code', (child.innerText || child.textContent || '').trim());
      } else if (tag === 'TABLE') {
        table(child);
      } else if (child.querySelector(BLOCK_SEL)) {
        walk(child);
      } else if (tag !== 'A' && tag !== 'IMG' && tag !== 'FIGURE') {
        const t = text(child);
        if (t.split(' ').length >= 3) push('p', t);
      }
    }
  };
  walk(root);

  const words = sections.reduce(
    (n, s) => n + s.heading.split(' ').length + s.blocks.reduce((m, b) => m + (b.text || '').split(' ').length, 0),
    0,
  );
  const site = (document.querySelector('meta[property="og:site_name"]') || {}).content || location.hostname;
  return {
    rhapsode: 1,
    url: location.href,
    title,
    site,
    lang: document.documentElement.lang || 'en',
    words,
    sections: sections.filter((s) => s.blocks.length || s.level <= 2),
  };
}
