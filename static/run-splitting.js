// Matches generation_limits.split_runs, including annotations, Unicode and whitespace.
const SpeechRuns = (() => {
  const length = text => text.length - (text.match(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g)?.length || 0);
  function offset(text, points) {
    let extra = 0;
    for (const match of text.matchAll(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g)) {
      if (match.index >= points + extra) break;
      extra++;
    }
    return points + extra;
  }
  function count(text, limit) {
    let runs = 0;
    let size = length(text);
    while (size > limit) {
      const remaining = Math.ceil(size / limit);
      const floor = Math.max(limit - 8000, size - limit * (remaining - 1));
      const low = offset(text, floor), high = offset(text, limit);
      let cut = high;
      const boundary = /\n\s*\n|[.!?؟。！？]["'”’»)]*(?=\s)|\s+/gu;
      for (const match of text.slice(low, high).matchAll(boundary)) cut = low + match.index + match[0].length;
      const near = Math.max(0, cut - 256);
      for (const match of text.slice(near, cut + 256).matchAll(/<\|[^<>\n]+\|>|<[^<>\n]+\|[^<>\n]+>|\[[^\]\n]{1,40}\]/gu)) {
        if (near + match.index < cut && cut < near + match.index + match[0].length) { cut = near + match.index; break; }
      }
      const beforeMarks = cut;
      while (cut > 1 && /\p{M}/u.test(String.fromCodePoint(text.codePointAt(cut)))) {
        cut -= /[\uDC00-\uDFFF]/u.test(text[cut - 1]) ? 2 : 1;
        // The API rejects pathological combining sequences; keep the editor responsive.
        if (beforeMarks - cut > 512) return runs + Math.ceil(size / limit);
      }
      if (text.slice(0, cut).trimEnd()) runs++;
      text = text.slice(cut).trimStart();
      size = length(text);
    }
    return runs + (text ? 1 : 0);
  }
  return {count};
})();
if (typeof module !== 'undefined') module.exports = SpeechRuns;
